function normalizePemCertificateInput(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  const normalized = text.includes("\\n") ? text.replace(/\\n/g, "\n") : text;
  const match = normalized.match(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/);
  if (!match) {
    return normalized;
  }
  const body = match[0]
    .replace(/-----BEGIN CERTIFICATE-----/g, "")
    .replace(/-----END CERTIFICATE-----/g, "")
    .replace(/\s+/g, "");
  const wrapped = body.match(/.{1,64}/g)?.join("\n") || body;
  return `-----BEGIN CERTIFICATE-----\n${wrapped}\n-----END CERTIFICATE-----`;
}

module.exports = {
  normalizePemCertificateInput,
};
