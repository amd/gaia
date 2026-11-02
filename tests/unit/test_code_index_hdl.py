# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Verilog, SystemVerilog and VHDL files are chunked at design units (#871)."""

import hashlib
from pathlib import Path

import pytest

from gaia.code_index.parsers import chunk_code_file, detect_language

VERILOG = """\
// Clock divider: toggles out every N cycles.
// module not_a_module_in_a_comment
module clk_div #(parameter N = 4) (
  input  wire clk,
  input  wire rst,
  output reg  out
);
  reg [7:0] cnt;

  always @(posedge clk or posedge rst) begin
    if (rst) cnt <= 0;
    else     cnt <= cnt + 1;
  end

  function [7:0] inc;
    input [7:0] x;
    inc = x + 1;
  endfunction
endmodule

module top (input wire clk, output wire y);
  clk_div #(.N(2)) u_div (.clk(clk), .rst(1'b0), .out(y));
endmodule
"""

SYSTEMVERILOG = """\
package bus_pkg;
  typedef logic [31:0] word_t;
endpackage

interface axi_if (input logic clk);
  logic valid;
endinterface

module fifo #(parameter DEPTH = 8) (input logic clk);
  always_ff @(posedge clk) begin
    wr_ptr <= wr_ptr + 1;
  end
  always_comb begin
    full = (count == DEPTH);
  end
  function automatic logic [7:0] next_ptr(input logic [7:0] p);
    return p + 1;
  endfunction
  task automatic reset_all;
    count = 0;
  endtask
endmodule

virtual class base_seq;
  pure virtual function void body();
endclass
"""

VHDL = """\
library ieee;
use ieee.std_logic_1164.all;

entity Clock_Divider is
  generic (N : integer := 4);
  port (clk : in std_logic; q : out std_logic);
end entity Clock_Divider;

ARCHITECTURE rtl OF Clock_Divider IS
  signal cnt : integer := 0;
begin
  div_proc: process(clk)
  begin
    if rising_edge(clk) then
      cnt <= cnt + 1;
    end if;
  end process div_proc;

  process(cnt) begin
    q <= '1';
  end process;
end architecture rtl;

package util_pkg is
  function log2(x : natural) return natural;
end package util_pkg;

package body util_pkg is
  pure function log2(x : natural) return natural is
  begin
    return 0;
  end function;
  procedure reset_counter(signal c : out integer) is
  begin
    c <= 0;
  end procedure;
end package body util_pkg;
"""


def _symbols(path, content):
    return [(c.symbol_type, c.symbol_name) for c in chunk_code_file(path, content)]


def _chunk(chunks, symbol_type, name):
    (match,) = [
        c for c in chunks if c.symbol_type == symbol_type and c.symbol_name == name
    ]
    return match


class TestDetectLanguage:
    @pytest.mark.parametrize(
        "name,lang",
        [
            ("rtl/top.v", "verilog"),
            ("inc/defs.vh", "verilog"),
            ("rtl/fifo.sv", "systemverilog"),
            ("inc/pkg.svh", "systemverilog"),
            ("rtl/div.vhd", "vhdl"),
            ("rtl/div.vhdl", "vhdl"),
            ("RTL/DIV.VHD", "vhdl"),
            ("Top.V", "verilog"),
        ],
    )
    def test_hdl_extensions(self, name, lang):
        assert detect_language(name) == lang

    @pytest.mark.parametrize("name", ["notes.vtt", "x.svg", "a.vhdx", "b.verilog"])
    def test_lookalike_extensions_are_not_hdl(self, name):
        assert detect_language(name) not in {"verilog", "systemverilog", "vhdl"}


class TestVerilog:
    def test_module_and_always_boundaries(self):
        chunks = chunk_code_file("rtl/clk_div.v", VERILOG)
        assert [(c.symbol_type, c.symbol_name) for c in chunks] == [
            ("module", "clk_div"),
            ("always", "always"),
            ("function", "inc"),
            ("module", "top"),
        ]
        module = _chunk(chunks, "module", "clk_div")
        assert module.start_line == 3
        assert module.language == "verilog"
        assert "input  wire clk" in module.content
        always = _chunk(chunks, "always", "always")
        assert always.start_line == 10
        assert "posedge clk" in always.content
        top = _chunk(chunks, "module", "top")
        assert top.start_line == 21
        assert top.end_line == len(VERILOG.splitlines())

    def test_end_keywords_and_comments_open_no_chunk(self):
        names = {name for _, name in _symbols("a.v", VERILOG)}
        assert "not_a_module_in_a_comment" not in names
        assert not any(n and n.startswith("end") for n in names)

    def test_systemverilog_design_units(self):
        chunks = chunk_code_file("rtl/fifo.sv", SYSTEMVERILOG)
        assert [(c.symbol_type, c.symbol_name) for c in chunks] == [
            ("package", "bus_pkg"),
            ("interface", "axi_if"),
            ("module", "fifo"),
            ("always", "always_ff"),
            ("always", "always_comb"),
            ("function", "next_ptr"),
            ("function", "reset_all"),
            ("class", "base_seq"),
            ("function", "body"),
        ]
        assert {c.language for c in chunks} == {"systemverilog"}

    @pytest.mark.parametrize(
        "content, expected",
        [
            ("program automatic test;\nendprogram\n", ("program", "test")),
            ("program static test;\nendprogram\n", ("program", "test")),
            ("package automatic pkg;\nendpackage\n", ("package", "pkg")),
            ("interface automatic bus_if;\nendinterface\n", ("interface", "bus_if")),
            ("module static m;\nendmodule\n", ("module", "m")),
        ],
    )
    def test_lifetime_keyword_is_not_the_name(self, content, expected):
        assert _symbols("t.sv", content) == [expected]

    @pytest.mark.parametrize(
        "content, expected",
        [
            (
                "module \\esc ;\n  wire y;\nendmodule\nmodule real_one;\nendmodule\n",
                [("module", "\\esc"), ("module", "real_one")],
            ),
            (
                "module \\foo-bar (input a);\n  assign b = a;\nendmodule\n",
                [("module", "\\foo-bar")],
            ),
            ("macromodule \\m.x ;\nendmodule\n", [("module", "\\m.x")]),
            ("interface \\if$1 ;\nendinterface\n", [("interface", "\\if$1")]),
            ("program \\p+q ;\nendprogram\n", [("program", "\\p+q")]),
            ("package automatic \\pkg* ;\nendpackage\n", [("package", "\\pkg*")]),
            ("virtual class \\c! ;\nendclass\n", [("class", "\\c!")]),
        ],
    )
    def test_escaped_identifier_names_a_unit(self, content, expected):
        assert _symbols("e.sv", content) == expected

    def test_escaped_module_keeps_its_ports_and_body(self):
        content = (
            "module \\foo-bar (input a, output b);\n"
            "  assign b = a;\n"
            "endmodule\n"
            "module real_one;\n"
            "endmodule\n"
        )
        chunks = chunk_code_file("e.v", content)
        esc = _chunk(chunks, "module", "\\foo-bar")
        assert (esc.start_line, esc.end_line) == (1, 3)
        assert "input a, output b" in esc.content
        assert "assign b = a;" in esc.content
        assert "real_one" not in esc.content

    @pytest.mark.parametrize(
        "header, expected",
        [
            (
                '(* keep_hierarchy = "yes" *) module attr_m(input a, output b);',
                ("module", "attr_m"),
            ),
            ("(* a *) (* b = 1 *) module m2 (input a, output b);", ("module", "m2")),
            (
                "  (*dont_touch*)interface bus_if (input a, output b);",
                ("interface", "bus_if"),
            ),
            ("(* x *) package p; // input a, output b", ("package", "p")),
            ("(* x *) program automatic t(input a, output b);", ("program", "t")),
            ("(* x *) virtual class c; // input a, output b", ("class", "c")),
            ("(* keep *) module \\esc-m (input a, output b);", ("module", "\\esc-m")),
        ],
    )
    def test_same_line_attribute_before_unit(self, header, expected):
        content = f"{header}\n  assign b = a;\nend\nmodule after;\nendmodule\n"
        chunks = chunk_code_file("a.sv", content)
        assert [(c.symbol_type, c.symbol_name) for c in chunks] == [
            expected,
            ("module", "after"),
        ]
        unit = chunks[0]
        assert unit.start_line == 1
        assert unit.content.startswith(header)
        assert "input a, output b" in unit.content
        assert "assign b = a;" in unit.content

    @pytest.mark.parametrize(
        "line",
        [
            "  reg [3:0] module_cnt;",
            "  wire interface_ok, package_id, program_en, class_sel;",
            "  (* full_case, parallel_case *) case (sel)",
            "  (* keep *) wire k;",
            '  (* mark_debug = "true" *) reg [7:0] module_q;',
            '  initial $display("module \\\\x and \\\\ module y");',
            '  initial $display("\\n module fake;");',
            "  wire \\bus[0] ;",
            "  assign \\bus[0] = module_cnt[0];",
            "  // (* keep *) module commented_attr;",
        ],
    )
    def test_body_lines_open_no_chunk(self, line):
        content = f"module top;\n{line}\nendmodule\n"
        chunks = chunk_code_file("fp.sv", content)
        assert [(c.symbol_type, c.symbol_name) for c in chunks] == [("module", "top")]
        assert line in chunks[0].content

    def test_module_without_ports(self):
        assert _symbols("t.v", "module tb;\n  initial $finish;\nendmodule\n") == [
            ("module", "tb")
        ]

    def test_file_without_design_units_is_one_chunk(self):
        content = "`define WIDTH 8\n`define DEPTH 16\n"
        chunks = chunk_code_file("defs.vh", content)
        assert len(chunks) == 1
        assert chunks[0].symbol_name is None
        assert chunks[0].content == content

    def test_empty_file(self):
        assert chunk_code_file("empty.sv", "   \n\n") == []


class TestVhdl:
    def test_entity_architecture_and_process_boundaries(self):
        chunks = chunk_code_file("rtl/clock_divider.vhd", VHDL)
        assert [(c.symbol_type, c.symbol_name) for c in chunks] == [
            ("entity", "Clock_Divider"),
            ("architecture", "rtl"),
            ("process", "div_proc"),
            ("process", "process"),
            ("package", "util_pkg"),
            ("function", "log2"),
            ("package", "util_pkg"),
            ("function", "log2"),
            ("function", "reset_counter"),
        ]
        entity = _chunk(chunks, "entity", "Clock_Divider")
        assert entity.start_line == 4
        assert entity.end_line == 8
        assert "end entity Clock_Divider;" in entity.content
        arch = _chunk(chunks, "architecture", "rtl")
        assert arch.start_line == 9
        assert "signal cnt" in arch.content
        assert {c.language for c in chunks} == {"vhdl"}

    def test_keywords_are_case_insensitive(self):
        content = (
            "ENTITY Foo IS\nEND ENTITY;\nArchitecture Behav Of Foo Is\nbegin\nend;\n"
        )
        assert _symbols("f.vhdl", content) == [
            ("entity", "Foo"),
            ("architecture", "Behav"),
        ]

    def test_end_lines_and_instantiations_open_no_chunk(self):
        content = (
            "architecture a of b is\nbegin\n"
            "  u1: entity work.Clock_Divider port map (clk => clk);\n"
            "end architecture a;\n"
            "-- entity commented is\n"
        )
        assert _symbols("i.vhd", content) == [("architecture", "a")]

    @pytest.mark.parametrize(
        "content, expected, body",
        [
            (
                "configuration cfg of top is\n  for rtl\n  end for;\n"
                "end configuration cfg;\n",
                ("configuration", "cfg"),
                "for rtl",
            ),
            (
                "CONFIGURATION Cfg OF Top IS\n  FOR rtl\n  END FOR;\nEND;\n",
                ("configuration", "Cfg"),
                "FOR rtl",
            ),
            (
                "context ctx is\n  library ieee;\n  use ieee.std_logic_1164.all;\n"
                "end context ctx;\n",
                ("context", "ctx"),
                "library ieee;",
            ),
        ],
    )
    def test_configuration_and_context_units(self, content, expected, body):
        chunks = chunk_code_file("c.vhd", content)
        assert [(c.symbol_type, c.symbol_name) for c in chunks] == [expected]
        assert body in chunks[0].content

    def test_configuration_and_context_references_open_no_chunk(self):
        content = (
            "context work.ctx;\n"
            "architecture a of b is\n"
            "  for u1 : comp use configuration work.cfg;\n"
            "begin\n"
            "end architecture a;\n"
        )
        assert _symbols("r.vhd", content) == [("architecture", "a")]


class TestIndexing:
    def test_index_repository_indexes_hdl_files(self, tmp_path):
        np = pytest.importorskip("numpy")
        pytest.importorskip("faiss")
        from gaia.code_index.sdk import CodeIndexConfig, CodeIndexSDK

        (tmp_path / "rtl").mkdir()
        (tmp_path / "rtl" / "clk_div.v").write_text(VERILOG, encoding="utf-8")
        (tmp_path / "rtl" / "fifo.sv").write_text(SYSTEMVERILOG, encoding="utf-8")
        (tmp_path / "rtl" / "div.vhd").write_text(VHDL, encoding="utf-8")

        sdk = CodeIndexSDK(
            CodeIndexConfig(repo_path=str(tmp_path), cache_dir=str(tmp_path / "c"))
        )
        sdk._load_embedder = lambda: None
        embedded = []

        def embed(batch):
            embedded.extend(batch)
            vectors = []
            for text in batch:
                seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
                vectors.append(np.random.default_rng(seed).random(8).tolist())
            return vectors

        sdk._embed_batch_call = embed
        result = sdk.index_repository()

        assert result.files_indexed == 3
        files = {Path(c["file_path"]).as_posix() for c in sdk._metadata["chunks"]}
        assert files == {"rtl/clk_div.v", "rtl/fifo.sv", "rtl/div.vhd"}
        assert any(t.startswith("module: clk_div\n") for t in embedded)
        assert any(t.startswith("entity: Clock_Divider\n") for t in embedded)
