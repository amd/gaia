// The React dev page's app. qa-pane.mjs bundles it with React's development
// build from the Agent UI's node_modules, so component names stay readable.
import { createElement as h, useState } from "react";
import { createRoot } from "react-dom/client";

function SaveButton({ onSave }) {
  return h("button", { className: "primary", type: "button", onClick: onSave }, "Save");
}

function ProfileCard() {
  const [saved, setSaved] = useState(false);
  return h(
    "section",
    { className: "card" },
    h("h2", null, "Profile"),
    h(SaveButton, { onSave: () => setSaved(true) }),
    saved ? h("p", { role: "status" }, "Saved.") : null
  );
}

createRoot(document.getElementById("root")).render(h(ProfileCard));
