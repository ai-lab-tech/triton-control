export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;
export const PASSWORD_PATTERN =
  /^(?=.{12,128}$)(?!.*\s)(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).+$/;
export const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export const PASSWORD_POLICY_MESSAGE =
  "Password must be 12-128 characters, include uppercase, lowercase, digit, and special character, and must not contain whitespace.";
export const EMAIL_POLICY_MESSAGE = "Enter a valid email address.";

export function isValidPassword(password: string): boolean {
  return PASSWORD_PATTERN.test(password);
}

export function isValidEmail(email: string): boolean {
  return EMAIL_PATTERN.test(email.trim());
}
