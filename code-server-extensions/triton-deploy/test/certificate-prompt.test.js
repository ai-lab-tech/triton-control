const assert = require("node:assert/strict");
const test = require("node:test");

const { normalizePemCertificateInput } = require("../certificate-prompt");

test("normalizes pasted PEM certificate blocks", () => {
  const pasted = "  -----BEGIN CERTIFICATE-----\nABCDEF123456\n-----END CERTIFICATE-----  ";
  assert.equal(
    normalizePemCertificateInput(pasted),
    "-----BEGIN CERTIFICATE-----\nABCDEF123456\n-----END CERTIFICATE-----",
  );
});

test("normalizes escaped newline PEM input", () => {
  const pasted = "-----BEGIN CERTIFICATE-----\\nABCDEF123456\\n-----END CERTIFICATE-----";
  assert.equal(
    normalizePemCertificateInput(pasted),
    "-----BEGIN CERTIFICATE-----\nABCDEF123456\n-----END CERTIFICATE-----",
  );
});

test("passes through plain text when no PEM block is present", () => {
  assert.equal(normalizePemCertificateInput("modelname"), "modelname");
});
