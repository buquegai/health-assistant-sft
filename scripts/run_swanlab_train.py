"""启动一次带 SwanLab 记录的 LLaMA-Factory 训练。

API Key 只从 SWANLAB_API_KEY 环境变量读取，不写入项目配置或命令行参数。
"""

import os
from pathlib import Path

from omegaconf import OmegaConf

from llamafactory.train.tuner import run_exp


def main() -> None:
    api_key = os.environ.get("SWANLAB_API_KEY")
    if not api_key:
        raise SystemExit("缺少 SWANLAB_API_KEY 环境变量。")

    config_path = Path(__file__).resolve().parents[1] / "configs" / "psychology_sft_swanlab.yaml"
    config = OmegaConf.to_container(OmegaConf.load(config_path), resolve=True)
    config["swanlab_api_key"] = api_key
    run_exp(config)


if __name__ == "__main__":
    main()
