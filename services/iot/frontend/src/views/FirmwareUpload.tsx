import React, { useEffect, useRef, useState } from 'react';
import { useAuth } from '../auth/context';
import { importPrivateKey, sha256Hex, signManifest, type SigningKey } from '../crypto/signing';
import './FirmwareUpload.css';

// Outcome rides alongside the text rather than being sniffed back out of it,
// so rewording a message (translating it included) cannot change how it renders.
type Notice = { text: string; ok: boolean };

// The marker esp32/main/ota.cpp builds out of its own FIRMWARE_VERSION and
// DEVICE_MODEL. Read here only to fill the form in and say what was found; the
// server reads it again out of the bytes it received and is the authority on
// what gets stored.
const BUILD_TAG = /ESPOTA-BUILD\{model=([^;}]{1,64});version=([^;}]{1,32})\}/g;

type Inspection =
  | { state: 'idle' }
  | { state: 'checking' }
  | { state: 'found'; model: string; version: string }
  | { state: 'absent'; reason: string };

export default function FirmwareUpload({ onPublished }: { onPublished: () => void }) {
  const { session, authFetch } = useAuth();
  const formRef = useRef<HTMLFormElement>(null);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [selectedFileName, setSelectedFileName] = useState<string>('');
  const [inspection, setInspection] = useState<Inspection>({ state: 'idle' });
  const [model, setModel] = useState('');
  const [version, setVersion] = useState('');
  const [signature, setSignature] = useState('');
  const [fileBytes, setFileBytes] = useState<ArrayBuffer | null>(null);
  const [signingKey, setSigningKey] = useState<SigningKey | null>(null);
  const [keyName, setKeyName] = useState('');
  const [keyError, setKeyError] = useState<string | null>(null);

  const identified = inspection.state === 'found';
  const canPublish = session?.account.hasPublicKey ?? false;

  // Read the image as soon as it is picked, before anything else is filled in.
  // Both fields are cleared first: an image that names itself overwrites them,
  // and one that does not must not leave the previous image's values sitting
  // there looking like they describe this file.
  async function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    setSelectedFileName(file ? file.name : '');
    setNotice(null);
    setModel('');
    setVersion('');
    // The signature covers a hash of one exact file. Keeping the old one here
    // would let a signature for the previous pick ride along with this one,
    // and the only thing that would catch it is the server rejecting an upload
    // whose message is about the signature rather than about the swap.
    setSignature('');

    if (!file) {
      setFileBytes(null);
      setInspection({ state: 'idle' });
      return;
    }

    setInspection({ state: 'checking' });
    try {
      // latin1 maps every byte to one code unit, so byte offsets survive and
      // no sequence is dropped as invalid the way utf-8 decoding would.
      const bytes = await file.arrayBuffer();
      setFileBytes(bytes);
      const text = new TextDecoder('latin1').decode(bytes);
      const found = [...text.matchAll(BUILD_TAG)];

      if (found.length === 1) {
        const [, foundModel, foundVersion] = found[0];
        setModel(foundModel);
        setVersion(foundVersion);
        setInspection({ state: 'found', model: foundModel, version: foundVersion });
      } else if (found.length > 1) {
        setInspection({ state: 'absent', reason: '這個檔案裡有不只一個版本標記，伺服器會拒絕它。' });
      } else {
        setInspection({ state: 'absent', reason: '這個檔案沒有版本標記。' });
      }
    } catch {
      setFileBytes(null);
      setInspection({ state: 'absent', reason: '讀不到這個檔案的內容。' });
    }
  }

  async function handleKeyChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    setKeyError(null);
    setSignature('');
    if (!file) {
      setSigningKey(null);
      setKeyName('');
      return;
    }
    try {
      setSigningKey(await importPrivateKey(await file.text()));
      setKeyName(file.name);
    } catch (e) {
      setSigningKey(null);
      setKeyName('');
      setKeyError(e instanceof Error ? e.message : '讀不到這個私鑰。');
    }
  }

  // Signing is driven by what the signature covers rather than by the moment a
  // file is picked. Model and version are part of the manifest and are still
  // editable for an image that does not name itself, so a signature produced
  // when the file arrived would be stale by the time it is sent.
  useEffect(() => {
    if (!signingKey || !fileBytes || !model || !version) return;
    let current = true;
    (async () => {
      try {
        const sig = await signManifest(signingKey, model, version, await sha256Hex(fileBytes));
        if (current) setSignature(sig);
      } catch (e) {
        if (current) setKeyError(e instanceof Error ? e.message : '簽章失敗。');
      }
    })();
    return () => {
      current = false;
    };
  }, [signingKey, fileBytes, model, version]);

  async function handleSubmit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();

    const form = formRef.current;
    if (!form || !form.reportValidity()) {
      return;
    }

    setSubmitting(true);
    setNotice(null);

    try {
      const res = await authFetch('/backend/firmware/upload', {
        method: 'POST',
        body: new FormData(form),
      });

      if (res.status === 401) {
        setNotice({ text: '登入階段已過期，請重新登入。', ok: false });
        return;
      }
      // Several distinct causes share these codes, and the backend already
      // names which one in `detail`, so show it rather than mirroring the list.
      if (res.status === 400 || res.status === 409) {
        const body = await res.json().catch(() => null);
        setNotice({ text: body?.detail ?? `上傳失敗（HTTP ${res.status}）`, ok: false });
        return;
      }
      if (!res.ok) {
        setNotice({ text: `上傳失敗（HTTP ${res.status}）`, ok: false });
        return;
      }

      // What it was published as, not what was typed: an image that names
      // itself decides this, and the two can differ only in that the fields
      // were left empty.
      const body = await res.json().catch(() => null);
      const published = body?.model && body?.version ? `${body.model} ${body.version}` : '';
      setNotice({ text: published ? `韌體已發布：${published}。` : '韌體已發布。', ok: true });
      form.reset();
      setSelectedFileName('');
      setFileBytes(null);
      setModel('');
      setVersion('');
      setSignature('');
      setInspection({ state: 'idle' });
      // The list is fetched once when the session appears, so without this the
      // version just published is missing from it until the page is reloaded.
      onPublished();
    } catch {
      setNotice({ text: '無法連線到後端，請確認 API 伺服器正在執行。', ok: false });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="upload-container">
      <div className="card upload-card">
        <div className="upload-header">
          <h1 className="text-xl font-bold text-primary">發布韌體</h1>
          <p className="text-xs text-secondary">
            選私鑰就在這個瀏覽器分頁裡簽好，私鑰不會送出去。自己簽過的話，把簽章貼進下面那格。
          </p>
        </div>
        {/* Outside the form, and that is the point. Inside it, the private
            key would stay out of the upload only because the input carries no
            `name`, which is one attribute between a signing key and the wire.
            Here there is no arrangement of attributes that could send it. */}
        <div className="form-group">
          <label className="form-label" htmlFor="signing-key">簽章私鑰（.pem）</label>
          <div className="dropzone">
            <input
              id="signing-key"
              type="file"
              className="dropzone-input"
              accept=".pem,.key"
              onChange={handleKeyChange}
            />
            <div className="dropzone-content">
              <span className="btn btn-secondary">
                + 選擇 .pem 檔
              </span>
              {keyName && !keyError
                ? <span className="form-help font-mono">{keyName}</span>
                : <span className="form-help">或拖曳檔案到這裡</span>}
            </div>
          </div>
          {keyError && (
            <div className="alert alert-error">
              <span className="alert-title">私鑰讀取失敗：</span>
              {keyError}
            </div>
          )}
        </div>

        <form ref={formRef} onSubmit={handleSubmit}>
          {/* Next to the key that produces it, rather than after the fields
              it covers. Filled in by the effect above whenever a key is
              loaded, and typed into only by someone who signed elsewhere.
              Inside the form because it is sent; the key above is not. */}
          <div className="form-group">
            <label className="form-label" htmlFor="firmware-signature">簽章</label>
            <textarea
              id="firmware-signature"
              name="signature"
              className="form-input font-mono"
              rows={3}
              placeholder={signingKey ? '選好下面的映像檔就會自動填' : ''}
              value={signature}
              onChange={event => setSignature(event.target.value)}
              style={{ resize: 'vertical' }}
              required
            />
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="firmware-file">韌體映像檔（.bin）</label>
            <div className="dropzone">
              <input
                id="firmware-file"
                type="file"
                name="firmware"
                className="dropzone-input"
                accept=".bin"
                required
                onChange={handleFileChange}
              />
              <div className="dropzone-content">
                <span className="btn btn-secondary">
                  + 選擇 .bin 檔
                </span>
                {selectedFileName
                  ? <span className="form-help font-mono">{selectedFileName}</span>
                  : <span className="form-help">或拖曳檔案到這裡</span>}
              </div>
            </div>
          </div>

          {inspection.state === 'checking' && (
            <div className="alert alert-info">正在讀取映像檔...</div>
          )}
          {inspection.state === 'found' && (
            <div className="alert alert-success">
              <span>
                <span className="alert-title">映像檔已辨識</span>
                這個檔案自己說它是 <strong className="font-mono">{inspection.model} {inspection.version}</strong>，
                下面兩格照它填好了，不能改。
              </span>
            </div>
          )}
          {inspection.state === 'absent' && (
            <div className="alert alert-warning">
              <span>
                <span className="alert-title">無法從映像檔判斷版本</span>
                {inspection.reason}請自己填下面兩格，並確認它們跟 sketch 裡的 DEVICE_MODEL 和
                FIRMWARE_VERSION 一致。
              </span>
            </div>
          )}

          {/* Free text, not a list. `model` is whatever string the device sends
              in `POST /api/check`, so a fixed list here is a second source of
              truth that silently blocks any board not on it. */}
          <div className="form-group">
            <label className="form-label" htmlFor="firmware-model">裝置型號</label>
            {/* readOnly, not disabled: a disabled field is left out of the
                FormData, and the server's check that the typed values agree
                with the image would then have nothing to compare. */}
            <input
              id="firmware-model"
              type="text"
              className="form-input"
              name="model"
              placeholder="ESP32"
              value={model}
              onChange={event => setModel(event.target.value)}
              readOnly={identified}
              required
            />
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="firmware-version">版本</label>
            <input
              id="firmware-version"
              type="text"
              className="form-input"
              name="version"
              placeholder="2.4.2"
              value={version}
              onChange={event => setVersion(event.target.value)}
              readOnly={identified}
              required
            />
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="firmware-notes">版本說明</label>
            <textarea
              id="firmware-notes"
              name="notes"
              className="form-input"
              rows={4}
              placeholder="這個版本改了什麼？"
              style={{ resize: 'vertical' }}
            />
          </div>

          {notice && (
            <div className={`alert ${notice.ok ? 'alert-info' : 'alert-error'}`}>
              {notice.text}
            </div>
          )}

          <button type="submit" className="btn btn-primary" style={{ width: '100%', marginTop: '0.9rem', padding: '0.82rem' }} disabled={submitting || inspection.state === 'checking' || !canPublish}>
            {inspection.state === 'checking' ? '讀取映像檔中...' : submitting ? '上傳中...' : !canPublish ? '要先設定簽章公鑰' : '上傳並發布'}
          </button>
        </form>
      </div>
    </div>
  );
}
