"""准备心理健康 SFT 数据；保留原始文件，按相同问题分组划分数据集。"""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(
        "/home/woei/datasets/psychology-10k-Deepseek-R1-zh/distill_psychology-10k-r1.json"))
    parser.add_argument("--output-dir", type=Path, default=Path(
        "/home/woei/datasets/psychology-sft-v1"))
    parser.add_argument("--model-path", default="/home/woei/models/Qwen3-1.7B")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cutoff-len", type=int, default=1536)
    parser.add_argument("--dry-run", action="store_true", help="只检查和显示统计，不写入文件")
    args = parser.parse_args()
    if args.output_dir.exists() and not args.dry_run:
        parser.error("输出目录已经存在。请检查原结果，或使用 --output-dir 指定新目录；不会覆盖现有数据。")

    groups = defaultdict(list)
    rejected = []
    total = 0
    with args.source.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            total += 1
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_no} 行 JSON 无法解析，未生成数据。") from exc
            if not isinstance(item, dict):
                raise ValueError(f"第 {line_no} 行不是 JSON 对象，未生成数据。")
            question, answer = item.get("input"), item.get("content")
            if (not isinstance(question, str) or not isinstance(answer, str)
                    or not question.strip() or not answer.strip()):
                rejected.append({"source_line": line_no, "reason": "问题或回答为空，或不是字符串"})
                continue
            question, answer = question.strip(), answer.strip()
            # 同题不同回答全部保留，但必须进入同一集合，避免跨集合泄漏。
            group_key = re.sub(r"\s+", "", question)
            record = {"instruction": question, "input": "", "output": answer}
            groups[group_key].append((line_no, record))

    if len(groups) < 10:
        raise ValueError("有效问题组不足 10 个，无法按此方案划分。")

    keys = sorted(groups)
    random.Random(args.seed).shuffle(keys)
    train_end, val_end = int(len(keys) * 0.8), int(len(keys) * 0.9)
    group_splits = {
        "train": keys[:train_end],
        "validation": keys[train_end:val_end],
        "test": keys[val_end:],
    }
    assert not (set(group_splits["train"]) & set(group_splits["validation"]))
    assert not (set(group_splits["train"]) & set(group_splits["test"]))
    assert not (set(group_splits["validation"]) & set(group_splits["test"]))
    splits = {name: [row for key in subset for row in groups[key]]
              for name, subset in group_splits.items()}

    # 使用实际训练模板统计词元，避免无声截断回答；仅加载本地分词器。
    from transformers import AutoTokenizer
    from llamafactory.data.template import get_template_and_fix_tokenizer
    from llamafactory.hparams import DataArguments

    tokenizer = AutoTokenizer.from_pretrained(args.model_path, local_files_only=True)
    template = get_template_and_fix_tokenizer(
        tokenizer, DataArguments(template="qwen3_nothink", enable_thinking=False))
    lengths = []
    for rows in groups.values():
        for _, row in rows:
            prompt, answer = template.encode_oneturn(tokenizer, [
                {"role": "user", "content": row["instruction"]},
                {"role": "assistant", "content": row["output"]},
            ])
            lengths.append(len(prompt) + len(answer))
    if max(lengths) > args.cutoff_len:
        raise ValueError(f"最长样本 {max(lengths)} 个词元，超过设定的 {args.cutoff_len}；请先调整长度方案。")

    # 试跑数据只取自训练集，不使用验证集或测试集。
    splits["smoke"] = random.Random(args.seed).sample(splits["train"], min(128, len(splits["train"])))
    summary = {
        "source_rows": total,
        "valid_rows": len(lengths),
        "rejected_rows": len(rejected),
        "unique_question_groups": len(groups),
        "split_rows": {name: len(rows) for name, rows in splits.items()},
        "max_tokens": max(lengths),
        "cutoff_len": args.cutoff_len,
        "seed": args.seed,
        "output_dir": str(args.output_dir),
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("检查通过，未写入文件。结构检查不等于心理健康内容审核。")
        return

    args.output_dir.mkdir(parents=True, exist_ok=False)
    for name, rows in splits.items():
        with (args.output_dir / f"{name}.jsonl").open("w", encoding="utf-8") as stream:
            for _, row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 登记文件放在专用数据目录，不改动 LLaMA-Factory 自带的登记文件。
    registry = {}
    for alias, split in (("psychology_train", "train"), ("psychology_val", "validation"),
                         ("psychology_smoke", "smoke")):
        registry[alias] = {
            "file_name": f"{split}.jsonl",
            "formatting": "alpaca",
            "columns": {"prompt": "instruction", "query": "input", "response": "output"},
        }
    (args.output_dir / "dataset_info.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        **summary,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_path": str(args.source),
        "source_sha256": sha256_file(args.source),
        "script_sha256": sha256_file(Path(__file__)),
        "template": "qwen3_nothink",
        "model_path": args.model_path,
        "normalization": "问题首尾去空格，按去除全部空白后的问题分组；固定种子按组划分 80/10/10",
        "field_mapping": {"input": "instruction", "content": "output", "reasoning_content": "排除"},
        "source_lines": {name: [line for line, _ in rows] for name, rows in splits.items()},
        "rejected": rejected,
        "output_sha256": {p.name: sha256_file(p) for p in sorted(args.output_dir.iterdir()) if p.is_file()},
        "review_status": "仅结构和长度检查；未进行逐条心理健康内容审核或语义近重复检查",
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("数据准备和登记完成。原始文件保持不变；测试集未登记为训练数据。")


if __name__ == "__main__":
    main()
