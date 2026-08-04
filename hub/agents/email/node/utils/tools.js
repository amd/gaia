/**
 * OpenAI ChatCompletionTool definitions for each pipeline step.
 *
 * Using forced tool calls instead of JSON-mode for two reasons:
 *   1. The model cannot deviate from the schema — enum validation is enforced
 *      by the API before we ever see the response.
 *   2. No regex/JSON-fence stripping required — arguments come pre-parsed.
 */

const CLASSIFY_TOOL = {
  type: "function",
  function: {
    name: "classify_email",
    description:
      "Classify the email into exactly one category and flag spam / phishing.",
    parameters: {
      type: "object",
      properties: {
        category: {
          type: "string",
          enum: ["URGENT", "NEEDS_RESPONSE", "FYI", "PROMOTIONAL", "PERSONAL"],
          description:
            "URGENT: needs immediate attention (alerts, deadlines <24h, legal). " +
            "NEEDS_RESPONSE: human question/request expecting a reply. " +
            "FYI: informational, no reply needed. " +
            "PROMOTIONAL: marketing, newsletters, receipts, automated notifications. " +
            "PERSONAL: personal/social, no work action.",
        },
        reason: {
          type: "string",
          description:
            "One sentence explaining why this category was chosen. " +
            "Reference the specific signal that decided it (e.g. sender pattern, " +
            "presence of a question, unsubscribe link, deadline).",
        },
        is_spam: {
          type: "boolean",
          description:
            "True for unsolicited bulk email; false for legitimate mail.",
        },
        is_phishing: {
          type: "boolean",
          description:
            "True when the email attempts to steal credentials or personal data.",
        },
      },
      required: ["reason", "category", "is_spam", "is_phishing"],
      additionalProperties: false,
    },
  },
};

const SUMMARIZE_TOOL = {
  type: "function",
  function: {
    name: "set_summary",
    description:
      "Provide a concise summary of the email or thread. " +
      "1–2 sentences, ≤300 characters, focused on the key ask or decision.",
    parameters: {
      type: "object",
      properties: {
        summary: {
          type: "string",
          description:
            "The summary. Must be ≤300 characters. Focus on the key ask, decision, or information.",
        },
      },
      required: ["summary"],
      additionalProperties: false,
    },
  },
};

const EMAIL_ADDRESS_SCHEMA = {
  type: "object",
  properties: {
    name: { type: "string", description: "Display name (optional)" },
    email: { type: "string", description: "Email address" },
  },
  required: ["email"],
  additionalProperties: false,
};

const SELECT_ACTIONS_TOOL = {
  type: "function",
  function: {
    name: "select_actions",
    description:
      "Decide which action types apply to this email. " +
      "Only select types clearly warranted by the content. Empty list is valid.",
    parameters: {
      type: "object",
      properties: {
        actions: {
          type: "array",
          uniqueItems: true,
          items: {
            type: "string",
            enum: [
              "link",
              "send_reply",
              "delete",
              "calendar_event",
              "unsubscribe",
            ],
          },
          description:
            "link: email contains URL(s) the recipient must act on. " +
            "send_reply: email is NEEDS_RESPONSE or URGENT and a reply is warranted. " +
            "delete: email is spam, phishing, promotional, or purely informational. " +
            "calendar_event: email contains a meeting request, invite, or clear event details. " +
            "unsubscribe: promotional email with an unsubscribe link.",
        },
      },
      required: ["actions"],
      additionalProperties: false,
    },
  },
};

const COMPOSE_DRAFT_TOOL = {
  type: "function",
  function: {
    name: "compose_draft",
    description:
      "Compose a complete, ready-to-send reply to this email. " +
      "Derive to/cc/bcc from the original headers and principal context.",
    parameters: {
      type: "object",
      properties: {
        to: {
          type: "array",
          items: EMAIL_ADDRESS_SCHEMA,
          description: "Primary recipients",
        },
        cc: {
          type: "array",
          items: EMAIL_ADDRESS_SCHEMA,
          description: "CC recipients (omit if none)",
        },
        bcc: {
          type: "array",
          items: EMAIL_ADDRESS_SCHEMA,
          description: "BCC recipients (omit if none)",
        },
        body: {
          type: "string",
          description:
            "Full email body. Professional, concise, no placeholder text. " +
            "Address every question or request raised in the original.",
        },
      },
      required: ["to", "body"],
      additionalProperties: false,
    },
  },
};

const EXTRACT_LINKS_TOOL = {
  type: "function",
  function: {
    name: "extract_links",
    description:
      "Extract all URLs from the email that require recipient action " +
      "(approve, view, download, respond, etc.). Exclude unsubscribe links.",
    parameters: {
      type: "object",
      properties: {
        items: {
          type: "array",
          items: {
            type: "object",
            properties: {
              description: {
                type: "string",
                description: "Short description of what the link does",
              },
              cta: {
                type: "string",
                description:
                  'Verb label for the CTA button, e.g. "View", "Approve", "Download"',
              },
              url: { type: "string" },
            },
            required: ["description", "cta", "url"],
            additionalProperties: false,
          },
        },
      },
      required: ["items"],
      additionalProperties: false,
    },
  },
};

const EXTRACT_CALENDAR_EVENT_TOOL = {
  type: "function",
  function: {
    name: "extract_calendar_event",
    description: "Extract meeting or event details from the email.",
    parameters: {
      type: "object",
      properties: {
        title: { type: "string", description: "Event title" },
        start: {
          type: "string",
          description:
            'ISO 8601 datetime of start (e.g. "2026-07-15T15:00:00"). ' +
            'Use "TBD" only if no date or time can be determined.',
        },
        end: {
          type: "string",
          description:
            'ISO 8601 datetime of end (e.g. "2026-07-15T16:00:00"). ' +
            "Omit if unknown.",
        },
        location: {
          type: "string",
          description: "Physical location or meeting link (omit if none)",
        },
        description: {
          type: "string",
          description: "Short event description or agenda (omit if none)",
        },
        rsvp: {
          type: "boolean",
          description:
            "true = accept, false = decline; omit if no RSVP decision is implied",
        },
      },
      required: ["title", "start"],
      additionalProperties: false,
    },
  },
};

const EXTRACT_UNSUBSCRIBE_TOOL = {
  type: "function",
  function: {
    name: "extract_unsubscribe",
    description: "Extract the unsubscribe URL from the email body.",
    parameters: {
      type: "object",
      properties: {
        url: { type: "string", description: "The unsubscribe URL" },
      },
      required: ["url"],
      additionalProperties: false,
    },
  },
};

const EXTRACT_DUE_DATE_TOOL = {
  type: "function",
  function: {
    name: "extract_due_date",
    description:
      "Determine whether the email contains an explicit deadline or due date. " +
      "Only extract when a date/time is clearly stated — do not infer from vague urgency.",
    parameters: {
      type: "object",
      properties: {
        has_deadline: {
          type: "boolean",
          description:
            "True only when a concrete deadline or due date is present in the email.",
        },
        due: {
          type: "string",
          description:
            "ISO 8601 datetime of the deadline. Required when has_deadline is true. " +
            "Resolve relative dates using today's date provided in the message.",
        },
      },
      required: ["has_deadline"],
      additionalProperties: false,
    },
  },
};

const EXTRACT_ACTIONS_TOOL = {
  type: "function",
  function: {
    name: "extract_actions",
    description:
      "Extract zero or more action items from the email. " +
      "Each item has a 'type' field that determines which other fields are needed. " +
      "Only include items that are clearly warranted — do not pad.",
    parameters: {
      type: "object",
      properties: {
        items: {
          type: "array",
          description: "List of action items. May be empty.",
          items: {
            type: "object",
            properties: {
              type: {
                type: "string",
                enum: [
                  "link",
                  "send_reply",
                  "delete",
                  "calendar_event",
                  "phone_call",
                  "unsubscribe",
                ],
                description:
                  "link: URL needing action — requires description, cta, url. " +
                  "send_reply: ready-to-send reply — requires to[], body. " +
                  "delete: move to trash — optional reason. " +
                  "calendar_event: create/RSVP event — requires title, start. " +
                  "phone_call: call a number — requires number. " +
                  "unsubscribe: one-click opt-out — requires url.",
              },
              description: {
                type: "string",
                description: "link: short description of what the link does",
              },
              cta: {
                type: "string",
                description:
                  'link: verb label for CTA button, e.g. "View", "Approve"',
              },
              url: {
                type: "string",
                description: "link / unsubscribe: the URL",
              },
              to: {
                type: "array",
                items: EMAIL_ADDRESS_SCHEMA,
                description: "send_reply: primary recipients",
              },
              cc: {
                type: "array",
                items: EMAIL_ADDRESS_SCHEMA,
                description: "send_reply: CC recipients",
              },
              bcc: {
                type: "array",
                items: EMAIL_ADDRESS_SCHEMA,
                description: "send_reply: BCC recipients",
              },
              body: {
                type: "string",
                description: "send_reply: full ready-to-send reply body",
              },
              reason: {
                type: "string",
                description: "delete: why",
              },
              title: {
                type: "string",
                description: "calendar_event: event title",
              },
              start: {
                type: "string",
                description: "calendar_event: ISO datetime of start",
              },
              end: {
                type: "string",
                description: "calendar_event: ISO datetime of end",
              },
              location: {
                type: "string",
                description: "calendar_event: location or meeting link",
              },
              rsvp: {
                type: "boolean",
                description:
                  "calendar_event: true = accept, false = decline; omit if no RSVP needed",
              },
              number: {
                type: "string",
                description: "phone_call: the phone number",
              },
            },
            required: ["type"],
            additionalProperties: false,
          },
        },
      },
      required: ["items"],
      additionalProperties: false,
    },
  },
};

module.exports = {
  CLASSIFY_TOOL,
  SUMMARIZE_TOOL,
  SELECT_ACTIONS_TOOL,
  COMPOSE_DRAFT_TOOL,
  EXTRACT_LINKS_TOOL,
  EXTRACT_CALENDAR_EVENT_TOOL,
  EXTRACT_UNSUBSCRIBE_TOOL,
  EXTRACT_DUE_DATE_TOOL,
  EXTRACT_ACTIONS_TOOL,
};
