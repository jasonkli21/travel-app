import { createElement } from "react";

export function ShoppingProjectSelect({ choices, selectedId, onChange, disabled = false }) {
  return createElement(
    "label",
    null,
    "Shopping project ",
    createElement(
      "select",
      {
        value: selectedId,
        disabled,
        onChange: (event) => onChange(event.currentTarget.value),
        "aria-label": "Shopping project",
      },
      createElement("option", { value: "" }, "Select an active project"),
      ...choices.map((choice) =>
        createElement(
          "option",
          { key: choice.project_id, value: choice.project_id },
          `${choice.label} (revision ${choice.revision})`,
        ),
      ),
    ),
  );
}
