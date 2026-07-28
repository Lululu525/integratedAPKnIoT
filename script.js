document.addEventListener("DOMContentLoaded", () => {
  const apkFrontendUrl = window.APIONIX_CONFIG?.apkFrontendUrl;
  if (apkFrontendUrl) {
    document.querySelectorAll("[data-apk-frontend]").forEach((link) => {
      link.href = apkFrontendUrl;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    });
  }

  const targets = document.querySelectorAll(".reveal, .text-rise");

  if (!("IntersectionObserver" in window)) {
    targets.forEach((el) => el.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      }
    });
  }, {
    threshold: 0.16,
    rootMargin: "0px 0px -70px 0px"
  });

  targets.forEach((el) => observer.observe(el));

  requestAnimationFrame(() => {
    document.querySelector(".site-header")?.classList.add("is-loaded");
  });
});
