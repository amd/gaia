/**
 * Rule-based pre-classifier.
 *
 * Runs before any LLM call. When `confident` is true the LLM classify step is
 * skipped entirely, saving a full round-trip.
 */

const TRANSACTIONAL_DOMAINS = [
  "stripe.com",
  "paypal.com",
  "braintreepayments.com",
  "square.com",
  "shopify.com",
  "shopifyemail.com",
  "quickbooks.com",
  "xero.com",
  "freshbooks.com",
  "recurly.com",
  "chargebee.com",
  "pump.co",
  "paddle.com",
  "gumroad.com",
  "invoice.paypal.com",
  "ups.com",
  "fedex.com",
  "usps.com",
  "dhl.com",
  "github.com",
  "gitlab.com",
  "bitbucket.org",
  "slack.com",
  "notion.so",
  "figma.com",
  "linear.app",
  "docusign.com",
  "hellosign.com",
  "adobesign.com",
  "echosign.com",
  "pandadoc.com",
  "signnow.com",
  "eversign.com",
  "okta.com",
  "auth0.com",
  "workday.com",
  "bamboohr.com",
  "gusto.com",
  "zendesk.com",
  "intercom.io",
  "zoom.us",
  "calendly.com",
  "amazonaws.com",
  "azure.com",
  "google.com",
  "atlassian.com",
  "jira.com",
  "pagerduty.com",
  "opsgenie.com",
  "statuspage.io",
  "heroku.com",
  "netlify.com",
  "vercel.com",
  "render.com",
  "cloudflare.com",
  "datadog.com",
  "newrelic.com",
  "sentry.io",
];

const AUTOMATED_FYI_SENDERS = [
  "noreply@",
  "no-reply@",
  "donotreply@",
  "do-not-reply@",
  "auto-confirm@",
  "notifications@",
  "alerts@",
  "automated@",
  "system@",
  "daemon@",
  "postmaster@",
  "mailer-daemon@",
];

const PROMOTIONAL_SENDERS_STRONG = [
  "newsletter",
  "marketing",
  "deals",
  "offers",
  "promotions",
  "mailer@",
  "bounce@",
  "bulk@",
  "campaign@",
  "digest@",
];

const PROMOTIONAL_SENDERS_WEAK = [
  "hello@",
  "info@",
  "updates@",
  "announce@",
  "announcements@",
];

const PROMOTIONAL_SUBJECT_TOKENS = [
  "unsubscribe",
  "sale",
  "% off",
  "discount",
  "coupon",
  "deal",
  "offer",
  "promo",
  "newsletter",
  "weekly digest",
  "monthly digest",
  "your order",
  "shipping",
  "invoice",
  "receipt",
  "confirmation",
];

const SPAM_SUBJECT_TOKENS = [
  "you won",
  "you have been selected",
  "claim your prize",
  "free gift",
  "make money",
  "earn $",
  "click here",
  "limited time",
  "act now",
  "urgent action required",
  "congratulations!",
  "account suspended",
  "verify your account",
  "unusual activity",
  "security alert",
];

const PHISHING_TOKENS = [
  "verify your credentials",
  "confirm your password",
  "login to secure",
  "click to verify",
  "your account will be closed",
  "immediate action required",
  "unusual sign-in activity",
];

const URGENT_SUBJECT_TOKENS = [
  "urgent",
  "asap",
  "critical",
  "time-sensitive",
  "action required",
  "immediate",
  "emergency",
  "deadline",
  "overdue",
  "alert",
];

const FYI_SUBJECT_TOKENS = [
  "fyi",
  "for your information",
  "heads up",
  "just so you know",
  "no action needed",
  "no action required",
  "for your awareness",
];

/**
 * @param {string} haystack
 * @param {string[]} tokens
 * @returns {string|undefined}
 */
function lcContains(haystack, tokens) {
  const lc = haystack.toLowerCase();
  return tokens.find((t) => lc.includes(t));
}

/**
 * @param {import('./types.js').EmailAddress} from
 * @returns {string}
 */
function senderString(from) {
  return `${from.name ?? ""} ${from.email}`.toLowerCase();
}

/**
 * @param {string} email
 * @returns {string|undefined}
 */
function matchTransactionalDomain(email) {
  const lc = email.toLowerCase();
  const atIdx = lc.lastIndexOf("@");
  if (atIdx === -1) return undefined;
  const domain = lc
    .slice(atIdx + 1)
    .replace(/>.*$/, "")
    .trim();
  return TRANSACTIONAL_DOMAINS.find(
    (td) => domain === td || domain.endsWith("." + td)
  );
}

/**
 * Try to extract an unsubscribe URL from a plain-text email body without an
 * LLM call.
 *
 * @param {string} body
 * @returns {string|undefined}
 */
function extractUnsubscribeUrl(body) {
  const URL_RE = /https?:\/\/[^\s<>"')\]]+/gi;
  const urls = body.match(URL_RE) ?? [];

  const OPTOUT_PATH_RE =
    /(?:unsubscribe|optout|opt-out|opt_out|email-preferences|manage-preferences|mailing-preferences|remove-me|removeme)/i;
  const byPath = urls.find((u) => OPTOUT_PATH_RE.test(u));
  if (byPath) return byPath;

  for (const line of body.split(/\r?\n/)) {
    if (/unsubscribe|opt.?out/i.test(line)) {
      const lineUrls = line.match(URL_RE);
      if (lineUrls?.[0]) return lineUrls[0];
    }
  }

  return undefined;
}

/**
 * Fast, zero-LLM pre-classification.
 *
 * @param {string} subject
 * @param {import('./types.js').EmailAddress} from
 * @param {string} body
 * @param {boolean} [isThread]
 * @returns {import('./types.js').HeuristicResult}
 */
function classifyHeuristic(subject, from, body, isThread = false) {
  const sender = senderString(from);
  const subjectLc = subject.toLowerCase();
  const bodyLc = body.toLowerCase();

  const phishingToken =
    lcContains(subjectLc, PHISHING_TOKENS) ??
    lcContains(bodyLc, PHISHING_TOKENS);
  if (phishingToken) {
    return {
      category: "URGENT",
      is_spam: false,
      spam_confident: true,
      is_phishing: true,
      confident: true,
      reason: `phishing signal: "${phishingToken}"`,
    };
  }

  const spamToken = lcContains(subjectLc, SPAM_SUBJECT_TOKENS);
  if (spamToken) {
    return {
      category: "PROMOTIONAL",
      is_spam: true,
      spam_confident: true,
      is_phishing: false,
      confident: true,
      reason: `spam signal in subject: "${spamToken}"`,
    };
  }

  const hasUnsubscribeLink =
    bodyLc.includes("unsubscribe") && bodyLc.includes("http");
  if (hasUnsubscribeLink) {
    return {
      category: "PROMOTIONAL",
      is_spam: false,
      spam_confident: true,
      is_phishing: false,
      confident: true,
      reason: "unsubscribe link in body",
    };
  }

  const transactionalDomain = matchTransactionalDomain(from.email);
  if (transactionalDomain) {
    return {
      category: "FYI",
      is_spam: false,
      spam_confident: true,
      is_phishing: false,
      confident: !isThread,
      reason: `transactional domain: "${transactionalDomain}"${isThread ? " (thread — LLM confirms)" : ""}`,
    };
  }

  const automatedSender = AUTOMATED_FYI_SENDERS.find((s) =>
    from.email.toLowerCase().includes(s)
  );
  if (automatedSender) {
    return {
      category: "FYI",
      is_spam: false,
      spam_confident: true,
      is_phishing: false,
      confident: !isThread,
      reason: `automated sender pattern: "${automatedSender}"${isThread ? " (thread — LLM confirms)" : ""}`,
    };
  }

  const promoSenderStrong = PROMOTIONAL_SENDERS_STRONG.find((s) =>
    sender.includes(s)
  );
  if (promoSenderStrong) {
    return {
      category: "PROMOTIONAL",
      is_spam: false,
      spam_confident: true,
      is_phishing: false,
      confident: true,
      reason: `promotional sender pattern: "${promoSenderStrong}"`,
    };
  }

  const promoSenderWeak = PROMOTIONAL_SENDERS_WEAK.find((s) =>
    sender.includes(s)
  );
  const hasPromoSubject = !!lcContains(subjectLc, PROMOTIONAL_SUBJECT_TOKENS);
  const hasUnsubBody =
    bodyLc.includes("unsubscribe") && bodyLc.includes("http");
  if (promoSenderWeak && (hasPromoSubject || hasUnsubBody)) {
    return {
      category: "PROMOTIONAL",
      is_spam: false,
      spam_confident: true,
      is_phishing: false,
      confident: true,
      reason: `promotional sender "${promoSenderWeak}" + ${hasUnsubBody ? "unsubscribe link" : `subject token "${lcContains(subjectLc, PROMOTIONAL_SUBJECT_TOKENS)}"`}`,
    };
  }

  const promoSubject = lcContains(subjectLc, PROMOTIONAL_SUBJECT_TOKENS);
  if (promoSubject) {
    return {
      category: "PROMOTIONAL",
      is_spam: false,
      spam_confident: true,
      is_phishing: false,
      confident: false,
      reason: `promotional subject token: "${promoSubject}"`,
    };
  }

  const fyiToken = lcContains(subjectLc, FYI_SUBJECT_TOKENS);
  if (fyiToken) {
    return {
      category: "FYI",
      is_spam: false,
      spam_confident: true,
      is_phishing: false,
      confident: true,
      reason: `FYI signal in subject: "${fyiToken}"`,
    };
  }

  const urgentToken = lcContains(subjectLc, URGENT_SUBJECT_TOKENS);
  if (urgentToken) {
    return {
      category: "URGENT",
      is_spam: false,
      spam_confident: true,
      is_phishing: false,
      confident: false,
      reason: `urgent signal in subject: "${urgentToken}"`,
    };
  }

  return {
    category: "FYI",
    is_spam: false,
    spam_confident: true,
    is_phishing: false,
    confident: false,
    reason: "no strong heuristic signal",
  };
}

/**
 * @type {Array<[RegExp, string]>}
 */
const SIGNING_URL_PATTERNS = [
  [/https?:\/\/[^\s"'<>]*\.?docusign\.net\/[^\s"'<>]+/i, "DocuSign"],
  [/https?:\/\/[^\s"'<>]*\.?docusign\.com\/[^\s"'<>]+/i, "DocuSign"],
  [/https?:\/\/[^\s"'<>]*\.?adobesign\.com\/[^\s"'<>]+/i, "Adobe Sign"],
  [/https?:\/\/[^\s"'<>]*\.?echosign\.com\/[^\s"'<>]+/i, "Adobe Sign"],
  [/https?:\/\/[^\s"'<>]*\.?hellosign\.com\/[^\s"'<>]+/i, "HelloSign"],
  [/https?:\/\/[^\s"'<>]*\.?pandadoc\.com\/[^\s"'<>]+/i, "PandaDoc"],
  [/https?:\/\/[^\s"'<>]*\.?signnow\.com\/[^\s"'<>]+/i, "SignNow"],
  [/https?:\/\/[^\s"'<>]*\.?eversign\.com\/[^\s"'<>]+/i, "eversign"],
];

/**
 * If the email body contains a recognised document-signing URL, returns the
 * URL and a human-readable service name. Returns `null` otherwise.
 *
 * @param {string} body
 * @returns {{ url: string, service: string } | null}
 */
function extractSigningUrl(body) {
  for (const [pattern, service] of SIGNING_URL_PATTERNS) {
    const match = body.match(pattern);
    if (match) return { url: match[0], service };
  }
  return null;
}

module.exports = {
  extractUnsubscribeUrl,
  classifyHeuristic,
  extractSigningUrl,
};
