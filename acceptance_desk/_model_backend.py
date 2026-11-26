"""由正式模型和试跑命令共用。仅在需要加载本地模型时导入。"""

from pathlib import Path

import torch
from transformers import AutoModel, AutoTokenizer, BertConfig, BertForSequenceClassification, BertTokenizer


class WeightMismatch(ValueError):
    def __init__(self, missing, unexpected):
        self.missing = missing
        self.unexpected = unexpected
        super().__init__(f"NLI 权重与结构不匹配：缺少 {missing}，多余 {unexpected}")


def load_nli(path: Path):
    tokenizer = BertTokenizer.from_pretrained(path, local_files_only=True)
    config = BertConfig.from_pretrained(path, local_files_only=True)
    model = BertForSequenceClassification(config)
    state = torch.load(path / "pytorch_model.bin", map_location="cpu", weights_only=True)
    result = model.load_state_dict(state, strict=False)
    missing = [key for key in result.missing_keys if not key.endswith("position_ids")]
    unexpected = [key for key in result.unexpected_keys if not key.endswith("position_ids")]
    if missing or unexpected:
        raise WeightMismatch(missing, unexpected)
    model.eval()
    return tokenizer, model


def load_embedding(path: Path):
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
    model = AutoModel.from_pretrained(path, local_files_only=True, use_safetensors=True)
    model.eval()
    return tokenizer, model
