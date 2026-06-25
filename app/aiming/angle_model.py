"""발사 각도 추론 MLP 회귀 모델.

입력: 객체 위치 + LiDAR 거리  ->  출력: 발사 pan / tilt 각도.

PyBullet CSV 로 학습하며(train/train_angle_mlp.py), 학습 시 저장한 입력/출력
정규화 통계를 함께 보관해 추론 시 동일하게 정규화/역정규화한다.

체크포인트(.pt) 구조:
    {
        "model_state": state_dict,
        "in_features": [...],   # 입력 컬럼 이름 순서
        "out_features": [...],  # 출력 컬럼 이름 순서 (예: ["pan", "tilt"])
        "x_mean", "x_std", "y_mean", "y_std": 정규화 통계 (list)
        "hidden": [64, 64],
    }
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

import torch
import torch.nn as nn


class AngleMLP(nn.Module):
    def __init__(self, in_dim: int, out_dim: int, hidden: Sequence[int] = (64, 64)) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        prev = in_dim
        for h in hidden:
            layers += [nn.Linear(prev, h), nn.ReLU()]
            prev = h
        layers.append(nn.Linear(prev, out_dim))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class AnglePredictor:
    """학습된 체크포인트를 로드해 정규화 포함 추론을 수행한다."""

    def __init__(self, ckpt_path: Path, device: str = "cpu") -> None:
        self.device = device
        ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
        self.in_features: list[str] = ckpt["in_features"]
        self.out_features: list[str] = ckpt["out_features"]
        self.x_mean = torch.tensor(ckpt["x_mean"], dtype=torch.float32, device=device)
        self.x_std = torch.tensor(ckpt["x_std"], dtype=torch.float32, device=device)
        self.y_mean = torch.tensor(ckpt["y_mean"], dtype=torch.float32, device=device)
        self.y_std = torch.tensor(ckpt["y_std"], dtype=torch.float32, device=device)

        self.model = AngleMLP(
            in_dim=len(self.in_features),
            out_dim=len(self.out_features),
            hidden=ckpt.get("hidden", (64, 64)),
        ).to(device)
        self.model.load_state_dict(ckpt["model_state"])
        self.model.eval()

    @torch.no_grad()
    def predict(self, features: Sequence[float]) -> dict[str, float]:
        """in_features 순서의 값 시퀀스를 받아 {out_feature: value} 로 반환."""
        x = torch.tensor(features, dtype=torch.float32, device=self.device)
        x = (x - self.x_mean) / self.x_std
        y = self.model(x.unsqueeze(0)).squeeze(0)
        y = y * self.y_std + self.y_mean
        return {name: float(y[i]) for i, name in enumerate(self.out_features)}


def load_predictor(ckpt_path: Optional[Path] = None, device: str = "cpu") -> Optional[AnglePredictor]:
    """체크포인트가 있으면 AnglePredictor 를, 없으면 None 을 반환한다."""
    from app.config import ANGLE_MODEL

    path = Path(ckpt_path) if ckpt_path else ANGLE_MODEL
    if not path.exists():
        return None
    return AnglePredictor(path, device=device)
