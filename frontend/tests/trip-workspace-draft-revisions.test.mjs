import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const frontendDirectory = path.resolve(testDirectory, "..");
const frontendRequire = createRequire(path.join(frontendDirectory, "package.json"));
const typescript = frontendRequire("typescript");
const realReact = frontendRequire("react");

function loadFormComponent(relativePath) {
  const componentPath = path.join(frontendDirectory, relativePath);
  const componentRequire = createRequire(componentPath);
  const state = [];
  let cursor = 0;
  const hookedReact = {
    ...realReact,
    useState(initialValue) {
      const index = cursor++;
      if (!(index in state)) {
        state[index] = typeof initialValue === "function" ? initialValue() : initialValue;
      }
      return [
        state[index],
        (nextValue) => {
          state[index] = typeof nextValue === "function" ? nextValue(state[index]) : nextValue;
        },
      ];
    },
  };
  const source = readFileSync(componentPath, "utf8");
  const compiled = typescript.transpileModule(source, {
    compilerOptions: {
      jsx: typescript.JsxEmit.ReactJSX,
      module: typescript.ModuleKind.CommonJS,
      target: typescript.ScriptTarget.ES2022,
    },
  });
  const compiledModule = { exports: {} };
  const componentRequireWithHooks = (specifier) => {
    if (specifier === "react") return hookedReact;
    if (specifier === "../../lib/errors") {
      return { errorMessage: () => "Something went wrong. Try again." };
    }
    return componentRequire(specifier);
  };
  new Function("require", "module", "exports", compiled.outputText)(
    componentRequireWithHooks,
    compiledModule,
    compiledModule.exports,
  );

  return {
    render(props) {
      cursor = 0;
      return compiledModule.exports.default(props);
    },
  };
}

function findElements(root, predicate, found = []) {
  if (Array.isArray(root)) {
    for (const child of root) findElements(child, predicate, found);
  } else if (root && typeof root === "object" && "props" in root) {
    if (predicate(root)) found.push(root);
    findElements(root.props.children, predicate, found);
  }
  return found;
}

function findInput(root, value) {
  return findElements(root, (element) => element.type === "input" && element.props.value === value)[0];
}

test("an itinerary item draft keeps its opening revision and blocks after workspace refresh", async () => {
  const formComponent = loadFormComponent("components/trip-workspace/item-form.tsx");
  const submitted = [];
  const baseProps = {
    trip: { id: "trip-1", revision: 4, days: [{ id: "day-1", day_index: 1 }] },
    dayId: "day-1",
    places: [],
    reservations: [],
    initial: {
      id: "item-1",
      title: "Old item",
      item_type: "activity",
      status: "planned",
      start_time: "10:00",
      end_time: null,
      notes: "old notes",
      place: null,
      reservation: null,
    },
    pending: false,
    disabled: false,
    onSubmit: async (input, expectedRevision) => submitted.push({ input, expectedRevision }),
    onCreatePlace: async () => ({ id: "place-1", name: "Cafe" }),
  };

  let tree = formComponent.render(baseProps);
  findInput(tree, "Old item").props.onChange({ target: { value: "My item draft" } });
  tree = formComponent.render(baseProps);
  let form = findElements(tree, (element) => element.type === "form")[0];
  await form.props.onSubmit({ preventDefault() {} });
  assert.equal(submitted.length, 1);
  assert.equal(submitted[0].expectedRevision, 4);
  assert.equal(submitted[0].input.title, "My item draft");

  tree = formComponent.render({
    ...baseProps,
    trip: { ...baseProps.trip, revision: 5 },
    initial: { ...baseProps.initial, title: "Other tab's update" },
  });
  form = findElements(tree, (element) => element.type === "form")[0];
  assert.equal(findInput(tree, "My item draft")?.props.value, "My item draft");
  assert.equal(findElements(tree, (element) => element.type === "fieldset")[0].props.disabled, true);
  assert.ok(findElements(tree, (element) => element.type === "p" && element.props.role === "status").length);

  await form.props.onSubmit({ preventDefault() {} });
  assert.equal(submitted.length, 1, "a stale draft must not submit after a refresh");
});

test("a place edit keeps its opening place revision and blocks after refresh", async () => {
  const formComponent = loadFormComponent("components/trip-workspace/place-form.tsx");
  const submitted = [];
  const baseProps = {
    initial: {
      id: "place-1",
      revision: 2,
      name: "Cafe",
      category: "food",
      address: "Old address",
      phone: null,
      website_url: null,
      latitude: null,
      longitude: null,
    },
    pending: false,
    disabled: false,
    onSubmit: async (input, expectedRevision) => submitted.push({ input, expectedRevision }),
  };

  let tree = formComponent.render(baseProps);
  findInput(tree, "Old address").props.onChange({ target: { value: "My place draft" } });
  tree = formComponent.render(baseProps);
  let form = findElements(tree, (element) => element.type === "form")[0];
  await form.props.onSubmit({ preventDefault() {} });
  assert.equal(submitted.length, 1);
  assert.equal(submitted[0].expectedRevision, 2);
  assert.equal(submitted[0].input.address, "My place draft");

  tree = formComponent.render({
    ...baseProps,
    initial: { ...baseProps.initial, revision: 3, address: "Other tab's update" },
  });
  form = findElements(tree, (element) => element.type === "form")[0];
  assert.equal(findInput(tree, "My place draft")?.props.value, "My place draft");
  assert.equal(findElements(tree, (element) => element.type === "fieldset")[0].props.disabled, true);
  assert.ok(findElements(tree, (element) => element.type === "p" && element.props.role === "status").length);

  await form.props.onSubmit({ preventDefault() {} });
  assert.equal(submitted.length, 1, "a stale draft must not submit after a refresh");
});
