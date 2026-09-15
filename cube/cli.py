"""Command line: fetch a point without writing a script.

    cube fetch --lat 22.54 --lon 114.06 --start 2024-01-01 --end 2024-12-31 \\
               --vars ghi,t2m,ws100 -o shenzhen.parquet

    cube verify --lat 40.0 --lon 116.0 --start 2025-01-01 --end 2025-06-30 \\
                --vars ghi --lead 1 --models ecmwf_ifs025,icon_seamless

    cube sources
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from . import api
from .schema import VARIABLES, UnitMismatch

SOURCE_TABLE = """\
用哪个源                                        来源            备注
──────────────────────────────────────────────────────────────────────────────
单点、过去的天气（资源评估、训练）             archive (ERA5)  ~1 秒；不能用来给预报打分
单点、未来的天气                                forecast        当前这一轮
给预报打分（固定时效）                          previous_runs   必须给 --lead
辐照度的真值                                    satellite       按区域选产品，与预报独立
成片区域、或要 37 层气压层                      ARCO-ERA5       惰性读 Zarr，可选依赖

要点：给预报打分只能用 previous_runs。普通的历史预报档案返回的是「每个小时当时
最好的那个预报」，时效不固定，按它算出来的分数不对应任何可以买到的产品。
"""


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--vars", default="ghi,t2m,ws10")
    parser.add_argument("-o", "--out", help="写入 parquet 或 csv")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存")


def _write(frame: pd.DataFrame, out: str | None) -> None:
    if not out:
        pd.set_option("display.width", 200)
        print(frame.head(12).round(2).to_string())
        print(f"\n{len(frame):,} 行 {frame.index.min()} → {frame.index.max()}")
        print(f"来源 {frame.attrs.get('source')} | 单位 {frame.attrs.get('units')}")
        return
    frame.to_csv(out) if out.endswith(".csv") else frame.to_parquet(out)
    print(f"{len(frame):,} 行写入 {out}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cube", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="取一个点的天气")
    _add_common(fetch)
    fetch.add_argument(
        "--purpose", default="resource", choices=["resource", "truth"],
        help="resource=当时的天气；truth=卫星反演，用作真值",
    )

    verify = sub.add_parser("verify", help="取固定时效的预报，用于打分")
    _add_common(verify)
    verify.add_argument("--lead", type=int, required=True, help="提前多少天")
    verify.add_argument("--models", help="逗号分隔，留空为默认")

    sub.add_parser("sources", help="打印「什么时候用哪个源」的决策表")
    sub.add_parser("vars", help="列出可取的变量与单位")

    args = parser.parse_args(argv)

    if args.command == "sources":
        print(SOURCE_TABLE)
        return 0
    if args.command == "vars":
        for name, spec in VARIABLES.items():
            kind = "累计量" if spec.accumulates else "瞬时量"
            print(f"  {name:8s} {spec.unit:8s} {kind}  {spec.description}")
        return 0

    try:
        frame = api.fetch(
            lat=args.lat, lon=args.lon, start=args.start, end=args.end,
            vars=args.vars,
            purpose="verify" if args.command == "verify" else args.purpose,
            lead_days=getattr(args, "lead", None),
            models=(args.models.split(",") if getattr(args, "models", None) else None),
            refresh=args.refresh,
        )
    except UnitMismatch as error:
        # Printed as a refusal rather than a traceback: this is the package
        # working, not failing.
        print(f"拒绝继续：{error}", file=sys.stderr)
        return 2
    except (KeyError, ValueError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1

    _write(frame, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
