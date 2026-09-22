"""HTTP endpoints for the OTA server.

Device protocol:

- `POST /api/check`
- `GET /api/download/{id}`

plus `POST /firmware/upload` for the dashboard to publish signed firmware and
`GET /api/devices` for its device page. Every dashboard route answers within
one account: the repositories take the account asking, so a handler that
forgot to pass it would not compile rather than serve someone else's rows.
Each handler reads the request, calls a use case, and returns a domain object;
the `response_model` on the route decides which fields reach the wire. Field
names and status codes follow what the ESP32 firmware in `esp32/main/ota.cpp`
expects.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import quote

from application.check_update import (
    CheckUpdate,
    CheckUpdateRequest,
    ModelMismatch,
    ModelNotFound,
    UnknownDevice,
)
from application.deactivate_firmware import DeactivateFirmware
from application.device_stats import DeviceStats
from application.register_device import (
    DeviceNotFound,
    RegisterDevice,
    SetDeviceEnabled,
)
from application.upload_firmware import (
    InvalidUploadIdentity,
    NoPublicKey,
    UploadFirmware,
    UploadFirmwareRequest,
)
from domain import fleet
from domain.firmware_image import MAX_FIRMWARE_BYTES, InvalidFirmwareImage
from domain.models import DeviceEvent, EventType, User
from domain.signing import InvalidManifestField, SignatureRejected
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from ports.repository import (
    DeviceEventRepository,
    DeviceRepository,
    FirmwareAlreadyExists,
    FirmwareBinaryAlreadyExists,
    FirmwareNotFound,
    FirmwareRepository,
)
from ports.storage import CHUNK_SIZE, StorageBackend
from pydantic import BaseModel, ConfigDict, Field

from api.auth import current_active_user
from api.deps import (
    get_check_update,
    get_deactivate_firmware,
    get_device_event_repository,
    get_device_repository,
    get_device_stats,
    get_firmware_repository,
    get_register_device,
    get_set_device_enabled,
    get_storage,
    get_upload_firmware,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class _FromDomain(BaseModel):
    """Base for responses read off a domain dataclass.

    Returning the dataclass itself would put every one of its fields on the
    wire, and adding a field to the domain would publish it silently. Naming
    the fields here keeps that decision in the route.
    """

    model_config = ConfigDict(from_attributes=True)


"""
Device protocol
"""


class CheckRequest(BaseModel):
    """What `ota.cpp:check()` sends.

    `device_id` and `device_secret` come out of that unit's `config.json` and
    are what the server identifies the caller by. Both are required, because an
    answer needs an account to look the firmware up in.

    The telemetry is required rather than optional for a different reason: a
    field that silently stopped arriving would surface as a null column on the
    dashboard instead of a 422 here.
    """

    model: str
    version: str
    device_id: str
    device_secret: str
    poll_interval_seconds: int
    rssi: int
    ip: str
    # Optional where the telemetry above is required, because there is nothing
    # to report until an update has actually failed. Requiring it would 422 a
    # healthy fleet off the dashboard. Bounded because this route is
    # unauthenticated and the value is stored and rendered.
    last_error: str | None = Field(default=None, max_length=64)
    failed_attempts: int | None = None


class CheckResponse(_FromDomain):
    """`exclude_none` on the route keeps the no-update answer a lone flag.

    `ota.cpp` reads `update_available` first and the rest only when it is true,
    so three explicit nulls would parse the same. Sending them anyway would put
    a shape on the wire that no deployed device was built against.
    """

    update_available: bool
    version: str | None = None
    signature: str | None = None
    download_url: str | None = None


@router.post("/api/check", response_model_exclude_none=True)
def check_update(
    body: CheckRequest,
    use_case: CheckUpdate = Depends(get_check_update),
) -> CheckResponse:
    try:
        result = use_case.execute(
            CheckUpdateRequest(
                model=body.model,
                version=body.version,
                device_id=body.device_id,
                device_secret=body.device_secret,
                poll_interval_seconds=body.poll_interval_seconds,
                rssi=body.rssi,
                ip=body.ip,
                last_error=body.last_error,
                failed_attempts=body.failed_attempts,
            )
        )
    except UnknownDevice as exc:
        # 401 and nothing else. Unregistered, wrong secret and disabled are one
        # answer on purpose: three different ones would let a caller work out
        # which device identifiers exist, and then which of them are live.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown device"
        ) from exc
    except ModelMismatch as exc:
        # Same bare 403 as a model with nothing published, so the two are not
        # distinguishable from outside. The log is where they separate, and it
        # is the only place an operator can see that a unit was registered
        # under one model and flashed with another.
        logger.warning("Check-in from %s: %s", body.device_id, exc)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN) from exc
    except ModelNotFound as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN) from exc

    return CheckResponse.model_validate(result)


def _content_disposition(filename: str) -> str:
    """Build a Content-Disposition value that survives any stored filename.

    `original_filename` is whatever the uploader's browser sent, and header
    values are latin-1 encoded on the way out, so a non-ASCII name raises
    instead of being sent. RFC 6266 answers this with two parameters: a quoted
    ASCII fallback, and a percent-encoded UTF-8 form that clients prefer when
    they understand it. Anything outside printable ASCII becomes an underscore
    in the fallback, which also keeps a quote or a newline in the name from
    escaping the quoted string.
    """
    ascii_name = "".join(
        c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in filename
    )
    # `safe=""`: the default leaves `/` alone, and this is a filename, not a path.
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


# Unauthenticated, and it has to stay that way: `ota.cpp` holds no account
# credential. The path segment is therefore the credential, which is why it is
# a random string rather than the row's id. See `Firmware.download_id`.
@router.get("/api/download/{download_id}")
def download_firmware(
    download_id: str,
    device_id: str | None = None,
    repo: FirmwareRepository = Depends(get_firmware_repository),
    storage: StorageBackend = Depends(get_storage),
    events: DeviceEventRepository = Depends(get_device_event_repository),
) -> Response:
    firmware = repo.get_by_download_id(download_id)
    if firmware is None or not storage.exists(firmware.filename):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # Recorded here rather than inside the generator below, which does not run
    # until the client starts reading. `device_id` comes from the query string
    # `/api/check` put on the URL it handed out; a cached or hand-typed URL
    # carries none, which records an unattributed download rather than nothing.
    events.add(
        DeviceEvent(
            device_id=device_id,
            event_type=EventType.DOWNLOAD,
            to_version=firmware.version,
        )
    )
    # The stored name is a hash. Offer a browser the name it was uploaded under;
    # the device ignores the header and reads the stream.
    download_name = firmware.original_filename or firmware.filename
    return StreamingResponse(
        storage.iter_chunks(firmware.filename),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(download_name),
            # Set explicitly, because a streaming response otherwise goes out
            # chunked with no length at all. `ota.cpp:469` reads the body with
            # `writeToStream`, whose only truncation check is comparing what it
            # copied against this header, so without it a short body flashes as
            # if it were whole. The length comes from the row and not from the
            # file on disk for the same reason: a blob truncated under us must
            # disagree with what the server promised, not quietly redefine it.
            "Content-Length": str(firmware.size_bytes),
        },
    )


class FirmwareResponse(_FromDomain):
    id: int
    model: str
    version: str
    filename: str
    original_filename: str | None
    signature: str
    sha256: str
    size_bytes: int
    notes: str | None
    active: bool
    created_at: datetime


@router.get("/api/firmware/list")
def firmware_list_api(
    user: User = Depends(current_active_user),
    repo: FirmwareRepository = Depends(get_firmware_repository),
) -> list[FirmwareResponse]:
    return [FirmwareResponse.model_validate(f) for f in repo.list_all(user.id)]


"""
Dashboard device page
"""


class DeviceResponse(BaseModel):
    """A device row plus the one thing about it the database does not hold.

    Built field by field rather than read off the dataclass, because `online`
    has no attribute to read: it is `fleet.is_online` answered against the
    clock at request time.
    """

    id: int
    device_id: str
    model: str
    current_version: str | None
    last_seen: datetime | None
    poll_interval_seconds: int | None
    rssi: int | None
    ip: str | None
    last_error: str | None
    failed_attempts: int | None
    enabled: bool
    online: bool | None


@router.get("/api/devices")
def device_list_api(
    user: User = Depends(current_active_user),
    repo: DeviceRepository = Depends(get_device_repository),
) -> list[DeviceResponse]:
    # One clock reading for the whole list. Sampling per device would let two
    # rows in the same response be judged against different moments.
    now = datetime.now(timezone.utc)
    return [
        DeviceResponse(
            id=d.id,
            device_id=d.device_id,
            model=d.model,
            current_version=d.current_version,
            last_seen=d.last_seen,
            poll_interval_seconds=d.poll_interval_seconds,
            rssi=d.rssi,
            ip=d.ip,
            last_error=d.last_error,
            failed_attempts=d.failed_attempts,
            enabled=d.enabled,
            online=fleet.is_online(d.last_seen, d.poll_interval_seconds, now),
        )
        for d in repo.list_all(user.id)
    ]


class RegisterDeviceRequest(BaseModel):
    model: str = Field(min_length=1, max_length=64)


class RegisteredDeviceResponse(BaseModel):
    """The only time the secret is ever sent anywhere.

    The server keeps a SHA-256 of it and nothing else, so there is no route
    that can show it again. Losing it means registering the unit afresh.
    """

    device_id: str
    device_secret: str
    model: str


@router.post("/api/devices", status_code=status.HTTP_201_CREATED)
def register_device(
    body: RegisterDeviceRequest,
    user: User = Depends(current_active_user),
    use_case: RegisterDevice = Depends(get_register_device),
) -> RegisteredDeviceResponse:
    registered = use_case.execute(body.model, user.id)
    return RegisteredDeviceResponse(
        device_id=registered.device.device_id,
        device_secret=registered.secret,
        model=registered.device.model,
    )


class DeviceStatsResponse(BaseModel):
    """The device page's summary counts.

    `unknown` is its own count rather than folded into `offline`: a device that
    has never checked in has told the server nothing, which is not the same as
    one that has stopped.
    """

    total: int
    online: int
    offline: int
    unknown: int
    behind_latest: int


# Kept above any future `/api/devices/{device_id}`, which would otherwise
# match "stats" and hand it to the handler as an id.
@router.get("/api/devices/stats")
def device_stats_api(
    user: User = Depends(current_active_user),
    use_case: DeviceStats = Depends(get_device_stats),
) -> DeviceStatsResponse:
    return DeviceStatsResponse.model_validate(use_case.execute(user.id), from_attributes=True)


"""
Admin firmware upload
"""


def _set_enabled(
    device_id: str, user: User, use_case: SetDeviceEnabled, enabled: bool
) -> DeviceResponse:
    try:
        device = use_case.execute(device_id, user.id, enabled)
    except DeviceNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    # `online` is answered against the clock, and a device just switched off has
    # not stopped reporting yet, so this says what is true right now rather than
    # what the operator is about to see happen.
    return DeviceResponse(
        id=device.id,
        device_id=device.device_id,
        model=device.model,
        current_version=device.current_version,
        last_seen=device.last_seen,
        poll_interval_seconds=device.poll_interval_seconds,
        rssi=device.rssi,
        ip=device.ip,
        last_error=device.last_error,
        failed_attempts=device.failed_attempts,
        enabled=device.enabled,
        online=fleet.is_online(
            device.last_seen, device.poll_interval_seconds, datetime.now(timezone.utc)
        ),
    )


@router.post("/api/devices/{device_id}/disable")
def disable_device(
    device_id: str,
    user: User = Depends(current_active_user),
    use_case: SetDeviceEnabled = Depends(get_set_device_enabled),
) -> DeviceResponse:
    return _set_enabled(device_id, user, use_case, False)


@router.post("/api/devices/{device_id}/enable")
def enable_device(
    device_id: str,
    user: User = Depends(current_active_user),
    use_case: SetDeviceEnabled = Depends(get_set_device_enabled),
) -> DeviceResponse:
    return _set_enabled(device_id, user, use_case, True)


class UploadResponse(BaseModel):
    status: str
    # What it was published as, which the uploader may not have typed: an image
    # carrying a build marker names itself.
    model: str
    version: str


# Headroom for what a multipart body carries besides the file: boundaries,
# part headers, and the model, version and notes fields. The declared length
# covers all of it, so comparing it against the ceiling directly would reject
# a build sitting exactly at the limit for the size of its own envelope.
MULTIPART_OVERHEAD_ALLOWANCE = 64 * 1024


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"Firmware must be at most {MAX_FIRMWARE_BYTES} bytes",
    )


def _read_upload_capped(request: Request, upload: UploadFile) -> bytes:
    """Read the uploaded file, refusing anything past the ceiling.

    Two checks, doing different jobs. The declared body length is the only one
    that can answer before the bytes are read, so an obviously oversized upload
    is rejected without touching the file. It is not the authority: the client
    writes that header, and it measures the whole multipart envelope rather
    than the part being read here.

    The capped read is the authority, and what it bounds is memory, not the
    transfer. Starlette has already spooled the request body by the time a
    handler runs, so nothing here stops the upload from arriving. It stops the
    server from holding an unbounded copy of it.
    """
    declared = request.headers.get("content-length")
    ceiling = MAX_FIRMWARE_BYTES + MULTIPART_OVERHEAD_ALLOWANCE
    if declared is not None and declared.isdigit() and int(declared) > ceiling:
        raise _too_large()

    chunks: list[bytes] = []
    total = 0
    while chunk := upload.file.read(CHUNK_SIZE):
        total += len(chunk)
        if total > MAX_FIRMWARE_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/firmware/upload", include_in_schema=False)
def upload(
    request: Request,
    user: User = Depends(current_active_user),
    firmware: UploadFile = File(...),
    # Optional because the image usually answers this. They are still read, and
    # a value that contradicts the image is refused rather than overwritten.
    model: str | None = Form(None),
    version: str | None = Form(None),
    notes: str | None = Form(None),
    # Required, and the one field the image cannot answer for itself. The
    # server holds no private key, so an upload with no signature is one
    # nothing can vouch for rather than one the server signs on the way past.
    signature: str = Form(...),
    use_case: UploadFirmware = Depends(get_upload_firmware),
) -> UploadResponse:
    data = _read_upload_capped(request, firmware)
    try:
        stored = use_case.execute(
            UploadFirmwareRequest(
                owner_id=user.id,
                owner_public_key=user.public_key,
                signature=signature,
                model=model,
                version=version,
                original_filename=firmware.filename or "firmware.bin",
                data=data,
                notes=notes,
            )
        )
    except (
        InvalidManifestField,
        InvalidFirmwareImage,
        InvalidUploadIdentity,
        SignatureRejected,
        NoPublicKey,
    ) as exc:
        # The validator's message names the field that failed, so pass it
        # through rather than flattening every rejection into one string.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FirmwareBinaryAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This binary was already uploaded as version {exc.existing_version}",
        ) from exc
    except FirmwareAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Version already exists for this model",
        ) from exc
    return UploadResponse(status="ok", model=stored.model, version=stored.version)


@router.post("/api/firmware/{firmware_id}/deactivate")
def deactivate_firmware(
    firmware_id: int,
    user: User = Depends(current_active_user),
    use_case: DeactivateFirmware = Depends(get_deactivate_firmware),
) -> FirmwareResponse:
    try:
        firmware = use_case.execute(firmware_id, user.id)
    except FirmwareNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    return FirmwareResponse.model_validate(firmware)
