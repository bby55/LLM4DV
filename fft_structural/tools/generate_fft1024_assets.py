"""Generate the twiddle ROMs required by the upstream fft1024_wide testbench."""

from __future__ import annotations

import argparse
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "upstream" / "generated" / "twiddle"
WIDTHS = (12, 17, 24)


def bits(value: int, width: int) -> str:
    return format(value % (1 << width), "0%db" % width)


def pair(real: float, imag: float, width: int, reduced: bool = False) -> str:
    scale = (1 << (width - 1)) - (1 if reduced else 0)
    # Upstream's non-reduced generator stores twBits+1 bits; its large ROM
    # stores twBits-1 bits because the generic denotes the signed precision.
    rom_width = width - 1 if reduced else width + 1
    real_i = max(-scale, min(scale - 1, int(round(real * scale))))
    imag_i = max(-scale, min(scale - 1, int(round(imag * scale))))
    return '"%s%s"' % (bits(imag_i, rom_width), bits(real_i, rom_width))


def simple_rom(size: int) -> str:
    depth = int(math.ceil(math.log(size, 2)))
    rows = []
    for width in WIDTHS:
        values = [pair(math.cos(2 * math.pi * i / size), -math.sin(2 * math.pi * i / size), width) for i in range(size)]
        inverse = [pair(math.cos(2 * math.pi * i / size), math.sin(2 * math.pi * i / size), width) for i in range(size)]
        rows.append(
            "g%d:\n\tif twBits = %d generate\n\t\tromInverse <= (\n\t\t\t%s\n\t\t);\n\t\trom <= (\n\t\t\t%s\n\t\t);\n\tend generate;" % (
                width,
                width,
                ",\n\t\t\t".join(inverse),
                ",\n\t\t\t".join(values),
            )
        )
    return """library ieee;
library work;
use ieee.numeric_std.all;
use ieee.std_logic_1164.all;
use work.fft_types.all;

entity twiddleGenerator{size} is
  generic(twBits: integer := 17; inverse: boolean := true);
  port(clk: in std_logic; twAddr: in unsigned({depth}-1 downto 0); twData: out complex);
end entity;
architecture a of twiddleGenerator{size} is
  constant romDepth: integer := 2**{depth};
  constant romWidth: integer := (twBits+1)*2;
  type ram1t is array(0 to romDepth-1) of std_logic_vector(romWidth-1 downto 0);
  signal rom, romInverse: ram1t;
  signal addr1: unsigned({depth}-1 downto 0);
  signal data0, data1: std_logic_vector(romWidth-1 downto 0);
begin
  addr1 <= twAddr when rising_edge(clk);
  data0 <= romInverse(to_integer(addr1)) when inverse else rom(to_integer(addr1));
  data1 <= data0 when rising_edge(clk);
  twData <= complex_unpack(data1);
{rows}
end a;
""".format(size=size, depth=depth, rows="\n".join(rows))


def large_rom(size: int) -> str:
    depth = int(math.ceil(math.log(size // 8, 2)))
    values = []
    for width in WIDTHS:
        entries = [pair(math.cos(2 * math.pi * (i + 1) / size), math.sin(2 * math.pi * (i + 1) / size), width, reduced=True) for i in range(size // 8)]
        values.append(
            "g%d:\n\tif twBits = %d generate\n\t\trom <= (\n\t\t\t%s\n\t\t);\n\tend generate;" % (width, width, ",\n\t\t\t".join(entries))
        )
    return """library ieee;
library work;
use ieee.numeric_std.all;
use ieee.std_logic_1164.all;

entity twiddleRom{size} is
  generic(twBits: integer := 17);
  port(clk: in std_logic; romAddr: in unsigned({depth}-1 downto 0); romData: out std_logic_vector((twBits-1)*2-1 downto 0));
end entity;
architecture a of twiddleRom{size} is
  constant romDepth: integer := 2**{depth};
  constant romWidth: integer := (twBits-1)*2;
  type ram1t is array(0 to romDepth-1) of std_logic_vector(romWidth-1 downto 0);
  signal rom: ram1t;
  signal addr1: unsigned({depth}-1 downto 0);
  signal data0, data1: std_logic_vector(romWidth-1 downto 0);
begin
  addr1 <= romAddr when rising_edge(clk);
  data0 <= rom(to_integer(addr1));
  data1 <= data0 when rising_edge(clk);
  romData <= data1;
{values}
end a;
""".format(size=size, depth=depth, values="\n".join(values))


def compact_rom(size: int) -> str:
    """Generate the quarter-wave ROM used by fft1024_wide_sub64."""
    depth = int(math.ceil(math.log(size // 8, 2)))
    rows = []
    for width in WIDTHS:
        entries = [
            pair(math.cos(2 * math.pi * (i + 1) / size), math.sin(2 * math.pi * (i + 1) / size), width, reduced=True)
            for i in range(size // 8)
        ]
        rows.append(
            "g%d:\n\tif twBits = %d generate\n\t\trom <= (\n\t\t\t%s\n\t\t);\n\tend generate;" % (
                width,
                width,
                ",\n\t\t\t".join(entries),
            )
        )
    return """library ieee;
library work;
use ieee.numeric_std.all;
use ieee.std_logic_1164.all;

entity twiddleRom{size} is
  generic(twBits: integer := 17);
  port(clk: in std_logic; romAddr: in unsigned({depth}-1 downto 0); romData: out std_logic_vector((twBits-1)*2-1 downto 0));
end entity;
architecture a of twiddleRom{size} is
  constant romDepth: integer := 2**{depth};
  constant romWidth: integer := (twBits-1)*2;
  type ram1t is array(0 to romDepth-1) of std_logic_vector(romWidth-1 downto 0);
  signal rom: ram1t;
  signal addr1: unsigned({depth}-1 downto 0);
  signal data0, data1: std_logic_vector(romWidth-1 downto 0);
begin
  addr1 <= romAddr when rising_edge(clk);
  data0 <= rom(to_integer(addr1));
  data1 <= data0 when rising_edge(clk);
  romData <= data1;
{rows}
end a;
""".format(size=size, depth=depth, rows="\n".join(rows))


def generate(output: Path = OUT) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    generated = []
    for size in (16, 64):
        path = output / ("twiddle_generator_%d.vhd" % size)
        path.write_text(simple_rom(size), encoding="utf-8")
        generated.append(path)
    path = output / "twiddle_rom_1024.vhd"
    path.write_text(large_rom(1024), encoding="utf-8")
    generated.append(path)
    path = output / "twiddle_rom_64.vhd"
    path.write_text(compact_rom(64), encoding="utf-8")
    generated.append(path)
    return generated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=OUT)
    args = parser.parse_args()
    for item in generate(args.out_dir):
        print(item)
