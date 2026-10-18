// API v1 renames in JavaScript and TypeScript, by tokens (docs/design/api-v1/rewrite.py calls it).
//   node rewrite_js.cjs RENAMES.json FILE...
// Renamed: identifiers (variables, properties, object keys), a string that is exactly a name, and the dot- or
// slash-separated parts of a string without spaces (a translation key, a URL path). Never: regular expressions, a
// string with spaces (text the user reads, a pattern a test looks for), JSX text, comments, hyphenated parts (CSS).
const fs = require("fs");
const parser = require(require.resolve("@babel/parser", { paths: [process.cwd()] }));

const renames = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const ren = (w) => (Object.prototype.hasOwnProperty.call(renames, w) ? renames[w] : w);
const parts = (s) => s.replace(/[^./{}$`'" ]+/g, (seg) => (/^[\w]+$/.test(seg) ? ren(seg) : seg));

for (const file of process.argv.slice(3)) {
  const src = fs.readFileSync(file, "utf8");
  let ast;
  try {
    ast = parser.parse(src, { sourceType: "module", plugins: ["typescript", "jsx"], tokens: true });
  } catch (e) {
    console.error(`${file}: not parsed, left as it is (${e.message})`);
    continue;
  }
  const edits = [];
  for (const t of ast.tokens) {
    const label = t.type.label || t.type;
    if (label === "name" || label === "jsxName") {
      if (ren(t.value) !== t.value) edits.push([t.start, t.end, ren(t.value)]);
    } else if (label === "string") {
      const raw = src.slice(t.start, t.end);
      const q = raw[0], body = raw.slice(1, -1);
      let out = body;
      if (ren(body) !== body) out = ren(body);
      else if (!/\s/.test(body) && /[./]/.test(body)) out = parts(body);
      if (out !== body) edits.push([t.start, t.end, q + out + q]);
    } else if (label === "template") {
      const raw = src.slice(t.start, t.end);
      if (raw && !/\s/.test(raw) && /[./]/.test(raw)) {
        const out = parts(raw);
        if (out !== raw) edits.push([t.start, t.end, out]);
      }
    }
  }
  if (!edits.length) continue;
  let out = src;
  for (const [a, b, s] of edits.sort((x, y) => y[0] - x[0])) out = out.slice(0, a) + s + out.slice(b);
  try {
    parser.parse(out, { sourceType: "module", plugins: ["typescript", "jsx"], errorRecovery: false });
  } catch (e) {
    console.error(`${file}: the renamed code does not parse (${e.message}): left as it is, by hand`);
    continue;
  }
  fs.writeFileSync(file, out);
  console.log(`${edits.length}\t${file}`);
}
