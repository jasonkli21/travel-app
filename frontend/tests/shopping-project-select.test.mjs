import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ShoppingProjectSelect } from "../components/trip-workspace/shopping-project-select.mjs";

test("Shopping project picker renders the destination-owned title and revision", () => {
  const markup = renderToStaticMarkup(
    createElement(ShoppingProjectSelect, {
      choices: [
        {
          project_id: "00000000-0000-0000-0000-000000000001",
          label: "Carry-on bag",
          revision: 4,
        },
      ],
      selectedId: "",
      onChange: () => undefined,
    }),
  );

  assert.match(markup, /aria-label="Shopping project"/);
  assert.match(markup, /Carry-on bag \(revision 4\)/);
  assert.doesNotMatch(markup, /Current project revision|Shopping project ID/);
});
