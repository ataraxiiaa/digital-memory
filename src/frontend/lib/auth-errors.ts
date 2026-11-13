type SignupError = { code?: string; status?: number };

export function signupErrorMessage(error: SignupError): string {
  const messages: Record<string, string> = {
    email_address_not_authorized: "Confirmation email can’t be sent to this address. The workspace owner needs to configure an email provider in Supabase before public signup can work.",
    email_address_invalid: "Supabase couldn’t accept this email address. Use a real email address rather than an example or test address.",
    signup_disabled: "New account creation is disabled. Please contact the workspace owner.",
    email_provider_disabled: "Email signup is disabled. Please contact the workspace owner.",
    weak_password: "Choose a stronger password that meets the account password requirements.",
    over_email_send_rate_limit: "The confirmation-email limit has been reached. Please wait before trying again.",
    over_request_rate_limit: "Too many attempts. Please wait before trying again.",
    captcha_failed: "Signup requires a security check that isn’t available here yet. Please contact the workspace owner.",
    unexpected_failure: "The account service couldn’t finish signup. Please contact the workspace owner to check the authentication logs.",
    request_timeout: "Signup timed out. Check your connection and try again.",
  };
  if (error.code && messages[error.code]) return messages[error.code];
  if (error.status === 429) return "Too many attempts. Please wait before trying again.";
  if (error.status === 0) return "Couldn’t reach signup. Check your connection and try again.";
  if (error.status && error.status >= 500) return "The account service couldn’t finish signup. Please contact the workspace owner to check email delivery and authentication logs.";
  // Preserve account-enumeration protection; do not disclose upstream messages.
  return "Couldn’t create an account. Check your details, or try signing in if you already have an account.";
}
