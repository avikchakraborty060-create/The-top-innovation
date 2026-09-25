import json

from skimread.outline import analyze, is_binary_path, render_outline

PY = '''"""doc"""
import os
from x import y

CONST = 1

class Foo(Base):
    def __init__(self, a: int) -> None:
        self.a = a

    @property
    def size(self):
        return 1

async def main(argv):
    pass

if __name__ == "__main__":
    main([])
'''

TS = '''import { x } from "./x";
export interface Props { a: number }
export class Widget extends Base {
  private count = 0;
  constructor(props: Props) { super(); }
  render(): string {
    if (this.count) {
      return "x";
    }
    return "y";
  }
  static create = (p: Props) => new Widget(p);
}
export function helper(a: string): number {
  return a.length;
}
export const arrow = async (x: number) => {
  return x;
};
describe("widget", () => {
  it("renders", () => {});
});
export default Widget;
'''

GO = '''package main

import "fmt"

type Server struct {
\tport int
}

func (s *Server) Start() error {
\treturn nil
}

func main() {
\tfmt.Println("hi")
}
'''

RS = '''use std::io;
pub struct Config { pub a: u32 }
impl Config {
    pub fn new() -> Self { Config { a: 1 } }
}
pub trait Run { fn run(&self); }
fn helper() {}
#[cfg(test)]
mod tests {
    #[test] fn t() {}
}
'''


def test_python_outline():
    o = analyze("x.py", PY)
    kinds = {(e.kind, e.name.split("(")[0]) for e in o.entries}
    assert ("class", "Foo") in kinds and ("def", "__init__") in kinds and ("async def", "main") in kinds
    assert ("var", "CONST") in kinds and ("imports", "2 import statements") in kinds
    foo = next(e for e in o.entries if e.name.startswith("Foo"))
    assert foo.start == 7 and foo.end == 13
    text = render_outline(o)
    assert "def __init__(self, a: int) -> None" in text and "@property" in text
    assert "__name__ == '__main__'" in text


def test_ts_outline():
    o = analyze("w.ts", TS)
    names = {e.name.split("(")[0] for e in o.entries}
    assert {"Props", "Widget", "render", "create", "helper", "arrow", "widget", "renders", "constructor"} <= names
    render = next(e for e in o.entries if e.name.startswith("render"))
    assert (render.start, render.end, render.depth) == (6, 11, 1)
    widget = next(e for e in o.entries if e.name == "Widget")
    assert widget.end == 13


def test_go_outline():
    o = analyze("m.go", GO)
    names = [e.name for e in o.entries]
    assert "Server" in names and "(Server).Start()" in names and "main()" in names
    srv = next(e for e in o.entries if e.name == "Server")
    assert srv.kind == "struct" and srv.end == 7


def test_rust_outline():
    o = analyze("l.rs", RS)
    names = {e.name for e in o.entries}
    assert {"Config", "new", "Run", "helper", "tests", "#[cfg(test)]"} <= names or {"Config", "new", "Run", "helper", "tests"} <= names


def test_markdown_json_yaml_csv_log():
    md = "# Title\ntext\n## Install\n```\n# not a heading\n```\n## Usage\nmore\n"
    o = analyze("r.md", md)
    assert [(e.name, e.start, e.end) for e in o.entries] == [("Title", 1, 2), ("Install", 3, 6), ("Usage", 7, 8)]

    js = json.dumps({"name": "x", "deps": {"a": 1, "b": 2}, "scripts": {"t": "x"}}, indent=2)
    o = analyze("p.json", js)
    assert [e.name for e in o.entries] == ["name", "deps", "scripts"]
    o = analyze("p.json", '{"a": 1, "b": 2}')
    assert "2 keys" in o.notes[0]

    o = analyze("c.yaml", "version: 1\nservices:\n  web:\n    image: x\nvolumes:\n  data: {}\n")
    assert [(e.name, e.start, e.end) for e in o.entries] == [("version", 1, 1), ("services", 2, 4), ("volumes", 5, 6)]

    o = analyze("d.csv", "id,name,score\n1,a,3\n2,b,4\n")
    assert "3 columns: id, name, score" in o.notes[0] and "2 data rows" in o.notes[1]

    log = "\n".join(["2024-01-01 10:00:00 INFO start"] * 5 + ["2024-01-01 10:00:07 ERROR boom", "2024-01-01 10:00:09 WARN slow"])
    o = analyze("app.log", log)
    text = render_outline(o)
    assert "1 ERROR lines at: 6" in text and "1 WARN lines at: 7" in text and "10:00:00 → 2024-01-01 10:00:09" in text


def test_shell_makefile_sql():
    o = analyze("Makefile", "all: build\n\techo hi\n\nbuild:\n\tgo build\n")
    assert [e.name for e in o.entries] == ["all", "build"]
    o = analyze("s.sh", "#!/bin/bash\nfoo() {\n  echo\n}\nfunction bar {\n}\n")
    assert "foo" in [e.name for e in o.entries]
    o = analyze("q.sql", "CREATE TABLE users (\n id int\n);\nSELECT * FROM users;\n")
    assert o.entries[0].name.startswith("CREATE TABLE users")


def test_fold_and_truncate_large_outline():
    src = "class Big:\n" + "".join(f"    def m{i}(self):\n        pass\n" for i in range(200))
    o = analyze("big.py", src)
    text = render_outline(o, max_entries=20)
    # cannot fold into one useless line: methods are listed and truncated with a note
    assert "class Big" in text and "def m0(self)" in text and "more entries between lines" in text
    src2 = "".join(f"class C{i}:\n    def a(self): pass\n    def b(self): pass\n" for i in range(30))
    text2 = render_outline(analyze("many.py", src2), max_entries=40)
    assert "2 members: a, b" in text2 and "class C29" in text2


def test_binary_and_unknown():
    assert is_binary_path("a.png") and not is_binary_path("a.py")
    o = analyze("notes.txt", "INTRO\nsome text\n\nDETAILS\nmore\n")
    assert [e.name for e in o.entries] == ["INTRO", "DETAILS"]
