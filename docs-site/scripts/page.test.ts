// The logic of pages/[...path].json, which is JSON with JavaScript bodies: it is run here on small
// trees, because the title, the one-h1 rule and the escaping of `${` are what readers and search see.
import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ROOT } from "./lib/config.ts";

const page = JSON.parse(readFileSync(join(ROOT, "pages", "[...path].json"), "utf8")) as {
  state: Record<string, { body?: string }>;
};

type Node = Record<string, any> | Node[] | string;
interface Toc {
  depth: number;
  id: string;
  text: string;
}

/** Evaluates the page's derived values for a document, in the order the compiler would. */
function derive(options: {
  title: string;
  children: Node[];
  toc?: Toc[];
  data?: Record<string, any>;
  name?: string;
}) {
  const state: Record<string, any> = {
    title: options.title,
    config: { data: { name: options.name ?? "frappe-nix" } },
    page: {
      $children: options.children,
      _meta: { toc: options.toc ?? [] },
      data: options.data ?? {},
    },
  };
  for (const key of ["lead", "titleNodes", "body", "tocNodes", "hasToc", "tagNodes", "pageTitle"])
    state[key] = new Function("state", page.state[key]!.body!)(state);
  return state;
}

const h = (tagName: string, id: string, text: string): Node => ({ tagName, id, children: [text] });
const p = (text: string): Node => ({ tagName: "p", children: [text] });
const tags = (nodes: Node[]) =>
  nodes.map((n) => (Array.isArray(n) || typeof n === "string" ? "?" : n.tagName));

test("the first h1 that says the title is the page's h1, wherever it stands", () => {
  const out = derive({
    title: "My Project",
    children: [
      p("badge"),
      h("h1", "my-project", "My Project"),
      p("text"),
      h("h2", "install", "Install"),
    ],
    toc: [
      { depth: 1, id: "my-project", text: "My Project" },
      { depth: 2, id: "install", text: "Install" },
    ],
  });
  expect(out.lead).toEqual({ kind: "title", id: "my-project", path: [1] });
  expect(out.titleNodes).toEqual([h("h1", "my-project", "My Project")]);
  expect(tags(out.body)).toEqual(["p", "p", "h2"]);
  expect(out.tocNodes).toHaveLength(1);
});

test("the match ignores case and punctuation, so a title and its heading may differ in markup", () => {
  const out = derive({ title: "C* and 2*3 notes", children: [h("h1", "c", "C* and 2*3 Notes")] });
  expect(out.lead.kind).toBe("title");
  expect(
    derive({ title: "my_file page", children: [h("h1", "x", "my file page")] }).lead.kind,
  ).toBe("title");
});

test("an h1 inside a container or a raw HTML block is found and lifted out", () => {
  const div = {
    tagName: "div",
    attributes: { align: "center" },
    children: [{ tagName: "img" }, h("h1", "t", "T")],
  };
  const out = derive({ title: "T", children: [div, p("x")] });
  expect(out.lead.path).toEqual([0, 1]);
  expect(out.titleNodes[0]).toEqual(h("h1", "t", "T"));
  expect(out.body[0]).toEqual({
    tagName: "div",
    attributes: { align: "center" },
    children: [{ tagName: "img" }],
  });
  // A raw HTML block arrives as a nested array.
  const raw = derive({
    title: "T",
    children: [[{ tagName: "div", children: [h("h1", "t", "T")] }], p("x")],
  });
  expect(raw.lead.path).toEqual([0, 0, 0]);
  expect(raw.body[0]).toEqual([{ tagName: "div", children: [] }]);
});

test("every other h1 becomes an h2, so a page has exactly one h1", () => {
  const out = derive({
    title: "A",
    children: [
      h("h1", "a", "A"),
      h("h1", "b", "Second"),
      { tagName: "blockquote", children: [h("h1", "c", "Quoted")] },
    ],
    toc: [
      { depth: 1, id: "a", text: "A" },
      { depth: 1, id: "b", text: "Second" },
      { depth: 1, id: "c", text: "Quoted" },
    ],
  });
  expect(out.titleNodes).toHaveLength(1);
  expect(out.body[0]).toEqual(h("h2", "b", "Second"));
  expect(out.body[1].children[0].tagName).toBe("h2");
  // The demoted headings are in the "On this page" list, the title is not.
  expect(out.tocNodes.map((li: any) => li.children[0].attributes.href)).toEqual(["#b", "#c"]);
  expect(out.tocNodes[0].children[0].attributes.class).toBe("d2");
});

test("without an h1 that says the title, the title is written from the data and every h1 is demoted", () => {
  const out = derive({
    title: "From frontmatter",
    children: [h("h1", "x", "Something else"), p("t")],
  });
  expect(out.lead).toEqual({ kind: "none", id: "", path: [] });
  expect(out.titleNodes).toEqual([
    { tagName: "h1", attributes: { id: "page-title" }, innerHTML: "From frontmatter" },
  ]);
  expect(tags(out.body)).toEqual(["h2", "p"]);
  expect(derive({ title: "T", children: [] }).titleNodes[0].tagName).toBe("h1");
});

test("text that came from frontmatter or headings stays text, whatever it holds", () => {
  const out = derive({
    title: "Set ${HOME} safely & <b>",
    children: [],
    toc: [{ depth: 2, id: "t", text: "Count ${1+1} <x>" }],
    data: { tags: ["t${1+1}", "a&b"] },
  });
  expect(out.titleNodes[0].innerHTML).toBe("Set &#36;{HOME} safely &amp; &lt;b&gt;");
  expect(out.titleNodes[0].textContent).toBeUndefined();
  expect(out.tocNodes[0].children[0].innerHTML).toBe("Count &#36;{1+1} &lt;x&gt;");
  expect(out.tagNodes.map((li: any) => li.innerHTML)).toEqual(["t&#36;{1+1}", "a&amp;b"]);
  // A heading in the body that holds a template-looking text is still found as the title.
  const found = derive({
    title: "Heading ${1+1} here",
    children: [
      {
        tagName: "h1",
        id: "h",
        children: [{ tagName: "span", innerHTML: "Heading &#36;{1+1} here" }],
      },
    ],
  });
  expect(found.lead.kind).toBe("title");
});

test("the home page's <title> does not say the project's name twice", () => {
  expect(derive({ title: "frappe-nix", children: [] }).pageTitle).toBe(
    "Documentation · frappe-nix",
  );
  expect(derive({ title: "Install", children: [] }).pageTitle).toBe("Install · frappe-nix");
  expect(derive({ title: "FRAPPE-NIX", children: [] }).pageTitle).toBe(
    "Documentation · frappe-nix",
  );
});

test("the landing page's cards show titles and descriptions as text, whatever they hold", () => {
  const landing = JSON.parse(readFileSync(join(ROOT, "pages", "index.json"), "utf8")) as {
    state: Record<string, { body?: string }>;
  };
  const state = {
    nav: {
      data: {
        featured: [
          {
            title: "Set ${HOME} safely & <b>",
            description: "Use ${1+1} & <angle> brackets.",
            url: "/docs/templated/",
            section: "Guides ${x}",
          },
          { title: "Plain", description: "", url: "/docs/plain/", section: "" },
        ],
      },
    },
  };
  const cards = new Function("state", landing.state.featuredNodes!.body!)(state) as Array<any>;
  const spans = (card: any) => card.children[0].children as Array<Record<string, any>>;
  expect(spans(cards[0]).map((span) => span.innerHTML)).toEqual([
    "Guides &#36;{x}",
    "Set &#36;{HOME} safely &amp; &lt;b&gt;",
    "Use &#36;{1+1} &amp; &lt;angle&gt; brackets.",
  ]);
  for (const card of cards)
    for (const span of spans(card)) expect(span.textContent).toBeUndefined();
  expect(spans(cards[1]).map((span) => span.innerHTML)).toEqual(["Documentation", "Plain", ""]);
  expect(cards[0].children[0].attributes.href).toBe("/docs/templated/");
});
