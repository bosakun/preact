"""Audit and reproduce the Japanese cognitive experiment report from raw episodes."""

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

from preact.cognition.benchmark import summarize
from preact.core.evidence import immediate_metric_scope
from preact.core.models import Prediction
from preact.domains.cognitive_queue import CognitiveQueueWorld

LABELS = {
    "reactive": "反応型（Gateなし）",
    "no_memory": "記憶利用なし",
    "fixed_prediction": "固定の楽観モデル",
    "no_adaptation": "適応なし",
    "current_preact": "既存PreAct・固定候補",
    "cognitive": "認知PreAct",
}


def audit(directory: Path) -> tuple[dict, dict]:
    report = json.loads((directory / "report.json").read_text())
    manifest = json.loads((directory / "manifest.json").read_text())
    source = Path(__file__).resolve().parents[1] / "src" / "preact"
    for relative, digest in manifest["source_sha256"].items():
        if hashlib.sha256((source / relative).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Current source differs from executed evidence: {relative}")
    expected = {
        (split, seed, condition)
        for split in manifest["splits"]
        for seed in manifest["protocol"][split]["seeds"]
        for condition in manifest["protocol"]["conditions"]
    }
    observed = set()
    raw_hashes = {}
    for row in report["episodes"]:
        key = row["split"], row["config"]["seed"], row["condition"]
        if key in observed:
            raise ValueError("Duplicate experiment episode")
        observed.add(key)
        path = Path(row["evidence"])
        raw = json.loads(path.read_text())
        if raw["metrics"] != row["metrics"] or raw["config"] != row["config"]:
            raise ValueError("Summary is not aligned to raw execution")
        world = CognitiveQueueWorld(**raw["config"])
        world.payload = raw["rounds"][-1]["final_state"]["payload"]
        outcomes = [e["data"]["observation"] for e in raw["events"] if e["kind"] == "outcome"]
        world.reward = sum(o["metrics"]["reward"] for o in outcomes)
        world.unsafe = any(o["unsafe"] for o in outcomes)
        computed = summarize(raw["events"], world, raw["rounds"], manifest["protocol"]["analysis"])
        if any(
            not math.isclose(value, row["metrics"][key], rel_tol=1e-12, abs_tol=1e-10)
            if isinstance(value, float)
            else value != row["metrics"][key]
            for key, value in computed.items()
        ):
            raise ValueError("Metrics cannot be reproduced from observed events")
        executed = {
            (e["run_id"], e["data"]["node_id"]) for e in raw["events"] if e["kind"] == "outcome"
        }
        predictions = {
            e["data"]["prediction"]["id"]: e for e in raw["events"] if e["kind"] == "prediction"
        }
        for label in raw["prediction_errors"]:
            event = predictions[label["prediction_id"]]
            prediction = Prediction.model_validate(event["data"]["prediction"])
            if (
                event["run_id"],
                event["data"]["node_id"],
            ) not in executed or not immediate_metric_scope(prediction):
                raise ValueError("An unexecuted or future prediction received a label")
        for run_id, node_id in executed:
            events = [e for e in raw["events"] if e["run_id"] == run_id]
            decisions = [
                e for e in events if e["kind"] == "decision" and e["data"].get("node_id") == node_id
            ]
            if row["condition"] != "reactive" and not any(
                e["kind"] == "authorization" for e in events
            ):
                raise ValueError("Execution lacks gate authorization")
            if not decisions or len([e for e in events if e["kind"] == "outcome"]) != 1:
                raise ValueError("A round did not execute exactly one observed action")
        raw_hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != expected:
        raise ValueError("Experiment evidence is incomplete")
    return report, manifest | {"raw_episode_sha256": raw_hashes}


def render(report: dict, manifest: dict, directory: Path) -> str:
    lines = [
        "# 認知queueの実測結果",
        "",
        "2026-10-08。Phase 1 の実装、Phase 2 の観測による推定更新、Phase 3 の限定比較を実行。",
        f"6構成 × 5シード × 2条件 = {len(report['episodes'])}エピソード。各episode最大60 tick。",
        "これはfirst-party queueのオンライン適応実験であり、汎用LLM agentの優位性の実証ではない。",
        "",
        "## 構成と評価方法",
        "",
        "反応型は観測queueの閾値だけで投入/排出し、既存Runtimeの明示的direct baselineを使う。",
        "記憶なしはledgerを残すがretrievalを無効化。適応なしはretrievalを行うがprior=3を固定。",
        "固定予測はservice=3を仮定する単一のqualifiedモデルで即時/未来を検証する。",
        "予測スコア用のunmeasured forecastは全構成で記録するが実行許可を与えない。",
        "既存PreActはWorldの固定候補順を使い、認知層の目標達成後の排出方針を持たない。",
        "認知版は実receiptからEMAを更新し、6 tickの仮説価値で候補順を変える。",
        "安全版は公開service>=1の占有上界を既存Gateへ渡す。学習で安全閾値を緩めない。",
        "",
        "開発条件はseed 0–4、high→low、変化tick=30、noise=0.1、容量8。",
        "別条件はseed 100–104、low→high、変化tick=23、noise=0.2、容量6。",
        "後者は事前に宣言した配置変更であり、学習済みepisode間転移や外部のblind benchmarkではない。",
        "全条件の乱数系列は対応し、隠れたschedule/変化時刻はplanner/engineに渡さない。",
        "報酬 = 処理数 − 0.25×queue占有 − 0.1×投入数。危険時にはepisodeを停止する。",
        "成功は終了tickで85件以上処理し、overflowがないこと。安全率はepisode単位。",
        "記憶なしと適応なしの行動/報酬が同じなのは、この最小モデルがretrieval経由でのみ学習するため。",
        "",
    ]
    for split, title in [("development", "開発条件"), ("transfer", "別条件")]:
        if split not in manifest["splits"]:
            continue
        lines += [
            f"## {title}",
            "",
            "| 構成 | 成功率 | 危険率 | 平均報酬 | 処理数予測MAE | 全処理完了Brier | ECE |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for condition in manifest["protocol"]["conditions"]:
            metrics = [
                r["metrics"]
                for r in report["episodes"]
                if r["split"] == split and r["condition"] == condition
            ]
            values = {
                k: statistics.mean(m[k] for m in metrics)
                for k in [
                    "success",
                    "unsafe",
                    "reward",
                    "model_processed_mae",
                    "model_all_processed_brier",
                    "model_all_processed_ece",
                ]
            }
            lines.append(
                f"| {LABELS[condition]} | {values['success']:.0%} | {values['unsafe']:.0%} | {values['reward']:.2f} | {values['model_processed_mae']:.3f} | {values['model_all_processed_brier']:.4f} | {values['model_all_processed_ece']:.4f} |"
            )
        paired = report["paired_reward_intervals"][split]["no_adaptation"]
        lines += [
            "",
            f"適応なしに対する対応シード報酬差: {paired['paired_reward_delta']:+.2f}、",
            f"seed bootstrap 95%区間 [{paired['bootstrap_95'][0]:.2f}, {paired['bootstrap_95'][1]:.2f}]。",
            "5シード・単一タスク族の区間であり、広い環境への統計的一般化を保証しない。",
            "",
        ]
    lines += [
        "## 効果と反例",
        "",
        "H1はこのqueueでは支持される。両条件で適応なしより報酬と処理数MAEが改善した。",
        "開発条件の全処理完了Brierは改善するが、別条件では悪化する。",
        "平均能力のEMAを確率混合モデルに使うと、少ない仕事では能力を観測できず、",
        "low→highの検出が遅れる。確率の校正改善は一般的には成立していない。",
        "別条件では既存PreAct/反応型が平均報酬で認知版を上回る。目標を満たすと投入を止める",
        "認知方針と、継続投入で報酬を増やすbaselineの目的差も含むため、目標制御の純粋な効果ではない。",
        "投入数/総処理数も減るので、throughput最大化に対する一方的な優位性はない。",
        "",
        "H2は本分布で支持される。bound版の危険率0に対し、楽観固定モデルは能力変化で失敗。",
        "これは公開された能力下限が正しい環境での結果であり、モデルの仮定が誤れば安全保証は失われる。",
        "H3は検証不足/予算不足/推定のみのnegative testでVERIFYまたはABSTAINを確認した。",
        "十分な予算の本比較では全episodeに実行可能な候補があり、round全体のABSTAINは0。",
        "candidateに対するhard vetoとepisodeの停止は区別する。",
        "",
        "## 適応とコスト",
        "",
    ]
    for split in manifest["splits"]:
        primary = [
            r["metrics"]
            for r in report["episodes"]
            if r["split"] == split and r["condition"] == "cognitive"
        ]
        fixed = [
            r["metrics"]
            for r in report["episodes"]
            if r["split"] == split and r["condition"] == "no_memory"
        ]
        lines += [
            f"- {split}: 認知版の予測回復tickは {[m['adaptation_ticks'] for m in primary]}。",
            f"  平均wall {statistics.mean(m['wall_seconds'] for m in primary):.2f}s / CPU {statistics.mean(m['cpu_seconds'] for m in primary):.2f}s、",
            f"  記憶なし平均wall {statistics.mean(m['wall_seconds'] for m in fixed):.2f}s。",
        ]
    lines += [
        "",
        "回復は真の分布平均±0.5に予測が3 tick連続で入る最初の位置。未来scheduleは分析のみで利用する。",
        "low→highでprior=3の固定版が0 tickになるのは初めから新分布平均に近いためで、学習の証拠ではない。",
        "horizon-1の成功/危険BrierはJSONに別記し、処理完了確率と混同しない。",
        "ECEは5bin、episode内sampleに基づく。安全forecastの間違いを平均能力の校正で隠さない。",
        "",
        "全60 tickを完了するbound条件は540 engine calls（9/tick）、360 escalation（6/tick）。",
        "360 callsは未選択候補に使われる。安全判断に寄与する棄却も含み、全て不要と認定はしない。",
        "同一requestのcache再利用回数も記録し、独立な証拠として数えない。",
        "本実装では検証call削減は実証していない。receipt整合の反復読出しで認知版は遅い。",
        "外部請求額は全て0、CPU/wall時間は実測。並行した回帰テスト等の影響もあるため速度比較は参考値。",
        "",
        "## 再現と証拠",
        "",
        "```bash",
        "uv run python -m preact.cognition.benchmark --output .cache/new-cognitive-evidence",
        "uv run python -m scripts.summarize_cognition .cache/new-cognitive-evidence \\",
        "  --output-doc .cache/new-cognitive-results.md --output-json .cache/new-cognitive-results.json",
        "uv run pytest -q tests/test_cognition.py",
        "```",
        "",
        f"今回のraw証拠: `{directory}`。manifest、各episodeのSQLite、events、実receipt、",
        "prediction/error rows、content-addressed artifacts、各sourceのSHA-256を保存した。",
        "[数値とsource/evidence hashes](../reports/cognitive-queue-v1-results.json)はrawから再計算・照合した。",
        "最初の実行 `.cache/cognitive-queue-v1-run1` も保持し、最終境界修正後に全60episodeを再実行した。",
        "既存凍結protocol/datasetは変更していない。benchmarkは新規outputを要求し、",
        "実行途中の失敗は成功結果で置換しない。途中results.jsonとSQLiteは残るが自動resumeは未実装。",
        "",
        "## 次のマイルストーン",
        "",
        "1. receiptの整合を保つ索引/読出し効率と、支配された候補の検証コスト削減。",
        "2. 需要で打ち切られた観測と変化検出を扱う確率belief、情報取得行動のablation。",
        "3. 目標達成率/throughput/holding lossを揃えた目的設定と多シード・別タスク族評価。",
        "4. 明示的なepisode間記憶転移、Crafter等の外部環境、必要性を測ってからlearned Engine。",
        "",
        "自発目標、procedural skill獲得、online NN学習、一般的な異種model合成、",
        "生物学的認知の忠実な再現、LLM-only比較は未実装/未実証。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output-doc", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    args = parser.parse_args()
    if args.output_doc.exists() or args.output_json.exists():
        raise FileExistsError("Use new output paths; existing evidence is preserved")
    report, manifest = audit(args.directory)
    args.output_doc.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_doc.write_text(render(report, manifest, args.directory))
    args.output_json.write_text(json.dumps({"manifest": manifest, **report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
