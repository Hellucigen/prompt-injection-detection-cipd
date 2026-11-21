# -*- coding: utf-8 -*-
"""评测主脚本：主对比表 + 家族级 F1 + 混淆衰减 + 错误案例导出 + 全部图表。
用法：
  python src/evaluate.py --fast          # 只跑 M1-M4（秒级）
  python src/evaluate.py --full          # 加上 M5 困惑度（CPU 较慢）
  python src/evaluate.py --judge         # 加上 M6（需 DEEPSEEK_API_KEY）
"""
from __future__ import annotations
import argparse, json, os, sys, time

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.metrics import (precision_recall_fscore_support, roc_auc_score,
                             confusion_matrix)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from methods import (METHOD_NAMES, FormatAnomalyDetector, SemanticDetector,
                     llm_judge, naive_bayes_fit, rule_predict, tfidf_char_fit,
                     tfidf_word_fit, vec_predict)

def load(name):
    return [json.loads(l) for l in
            open(os.path.join(ROOT, "data", f"{name}.jsonl"), encoding="utf-8")]

def metrics(y, p, thr=0.5):
    yhat = [1 if v >= thr else 0 for v in p]
    pr, rc, f1, _ = precision_recall_fscore_support(
        y, yhat, average="binary", zero_division=0)
    fpr = int(confusion_matrix(y, yhat, labels=[0, 1])[0, 1])
    n_neg = max(1, sum(1 for v in y if v == 0))
    auc = roc_auc_score(y, p) if len(set(y)) > 1 else float("nan")
    return {"precision": round(pr, 4), "recall": round(rc, 4),
            "f1": round(f1, 4), "fpr": round(fpr / n_neg, 4),
            "auc": round(float(auc), 4)}

def family_f1(rows, p, thr=0.5):
    from collections import defaultdict
    agg = defaultdict(lambda: ([], []))
    for r, v in zip(rows, p):
        if r["label"] == 1:
            agg[r["family"]][0].append(1)
            agg[r["family"]][1].append(1 if v >= thr else 0)
    from sklearn.metrics import f1_score
    return {k: round(f1_score(v[0], v[1], zero_division=0), 3)
            for k, v in agg.items()}

def save_errors(rows, probs_by_method, tag="test"):
    errs = []
    preds = {m: [1 if v >= 0.5 else 0 for v in ps]
             for m, ps in probs_by_method.items()}
    for i, r in enumerate(rows):
        wrong = [m for m, ys in preds.items() if ys[i] != r["label"]]
        if wrong and len(wrong) >= 2:      # 至少两种方法都错——有讨论价值
            errs.append({"text": r["text"][:120], "label": r["label"],
                         "family": r["family"], "wrong_methods": wrong})
    with open(os.path.join(ROOT, "results", f"errors_{tag}.json"), "w",
              encoding="utf-8") as f:
        json.dump(errs[:80], f, ensure_ascii=False, indent=1)
    return len(errs)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--judge", action="store_true")
    args = ap.parse_args()

    train, test, robust = load("train"), load("test"), load("robustness")
    y_test = [r["label"] for r in test]
    texts_test = [r["text"] for r in test]
    results, elapsed = {}, {}

    # ── 训练快方法 ──
    t0 = time.time()
    w_vec, w_clf = tfidf_word_fit(train)
    c_vec, c_clf = tfidf_char_fit(train)
    nb_vec, nb_clf = naive_bayes_fit(train)
    sem = SemanticDetector().fit(train)
    elapsed["训练 M2-M4"] = round(time.time() - t0, 1)

    probs = {}
    probs["M1_规则"] = rule_predict(texts_test)
    probs["M2_TF-IDF词级"] = vec_predict(w_vec, w_clf, texts_test)
    probs["M2b_朴素贝叶斯"] = vec_predict(nb_vec, nb_clf, texts_test)
    probs["M3_字符ngram"] = vec_predict(c_vec, c_clf, texts_test)
    probs["M4_语义bge"] = sem.predict(texts_test)

    fa = FormatAnomalyDetector().fit(train)
    probs["M5_格式异常"] = fa.predict(texts_test)
    if args.judge:
        probs["M6_LLM-judge"] = llm_judge(texts_test) or []

    for m, p in probs.items():
        if len(p) == len(y_test):
            results[m] = metrics(y_test, p)

    # ── 家族级 F1 ──
    fam = {m: family_f1(test, p) for m, p in probs.items() if len(p) == len(y_test)}

    # ── LOFO：留一家族外推（对"全新攻击类型"的泛化——安全场景灵魂问题）──
    fams_all = sorted({r["family"] for r in train if r["label"] == 1})
    lofo = {}
    for held in fams_all:
        tr_l = [r for r in train if not (r["label"] == 1 and r["family"] == held)]
        te_rows = ([r for r in test if r["label"] == 1 and r["family"] == held]
                   + [r for r in test if r["label"] == 0])
        y = [r["label"] for r in te_rows]
        tx = [r["text"] for r in te_rows]
        row = {}
        wv, wc = tfidf_word_fit(tr_l)
        cv, cc = tfidf_char_fit(tr_l)
        row["M2_TF-IDF词级"] = metrics(y, vec_predict(wv, wc, tx))["f1"]
        nv, nc = naive_bayes_fit(tr_l)
        row["M2b_朴素贝叶斯"] = metrics(y, vec_predict(nv, nc, tx))["f1"]
        row["M3_字符ngram"] = metrics(y, vec_predict(cv, cc, tx))["f1"]
        s = SemanticDetector().fit(tr_l)
        row["M4_语义bge"] = metrics(y, s.predict(tx))["f1"]
        row["M1_规则"] = metrics(y, rule_predict(tx))["f1"]
        lofo[held] = row
    lofo_avg = {m: round(float(np.mean([row[m] for row in lofo.values()])), 3)
                for m in next(iter(lofo.values()))}

    # ── 鲁棒性：混淆衰减 ──
    r_texts = [r["text"] for r in robust]
    r_probs = {
        "M1_规则": rule_predict(r_texts),
        "M2_TF-IDF词级": vec_predict(w_vec, w_clf, r_texts),
        "M2b_朴素贝叶斯": vec_predict(nb_vec, nb_clf, r_texts),
        "M3_字符ngram": vec_predict(c_vec, c_clf, r_texts),
        "M4_语义bge": sem.predict(r_texts),
    }
    r_y = [r["label"] for r in robust]
    levels = ["clean", "L1_装饰", "L2_同音", "L3_拆字", "L4_混合"]
    decay = {}
    for m, p in r_probs.items():
        row = {}
        for lv in levels:
            idx = [i for i, r in enumerate(robust)
                   if r.get("obfuscation") in (lv, lv) or (lv == "clean" and r.get("obfuscation") == "clean")]
            if lv != "clean":
                idx = [i for i, r in enumerate(robust) if r.get("obfuscation") == lv]
                idx = [i for i in idx if r_y[i] == 1] or idx
            yy = [r_y[i] for i in idx]
            pp = [p[i] for i in idx]
            if yy and len(set(yy)) > 1:
                row[lv] = metrics(yy, pp)["f1"]
            else:
                # clean 组内含正负例；混淆组只有正例 → 改报召回
                hit = sum(1 for v, t in zip(pp, yy) if (v >= 0.5) == bool(t))
                row[lv] = round(hit / max(1, len(yy)), 3)
        decay[m] = row
    # clean 级用完整混合子集
    clean_idx = [i for i, r in enumerate(robust) if r.get("obfuscation") == "clean"]
    for m, p in r_probs.items():
        yy = [r_y[i] for i in clean_idx]
        pp = [p[i] for i in clean_idx]
        if len(set(yy)) > 1:
            decay[m]["clean"] = metrics(yy, pp)["f1"]

    # ── 输出 ──
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    n_err = save_errors(test, {m: p for m, p in probs.items() if len(p) == len(y_test)})
    payload = {"main": results, "family_f1": fam, "decay": decay,
               "lofo": lofo, "lofo_avg": lofo_avg,
               "elapsed": elapsed, "n_errors_exported": n_err,
               "dataset": {"train": len(train), "test": len(test),
                           "robustness": len(robust)}}
    with open(os.path.join(ROOT, "results", "metrics.json"), "w",
              encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # ── 图表 ──
    figs = os.path.join(ROOT, "figures")
    # 图1：主对比
    ms = list(results.keys())
    x = np.arange(len(ms))
    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.bar(x - 0.2, [results[m]["f1"] for m in ms], 0.4, label="F1")
    ax.bar(x + 0.2, [results[m]["fpr"] for m in ms], 0.4, label="误杀率 FPR")
    ax.set_xticks(x); ax.set_xticklabels(ms, fontsize=9)
    ax.set_ylim(0, 1); ax.legend(); ax.set_title("主实验：五种检测方法对比（CIPD-Chat test）")
    for i, m in enumerate(ms):
        ax.text(i - 0.2, results[m]["f1"] + 0.02, f'{results[m]["f1"]:.2f}', ha="center", fontsize=8)
        ax.text(i + 0.2, results[m]["fpr"] + 0.02, f'{results[m]["fpr"]:.2f}', ha="center", fontsize=8)
    fig.tight_layout(); fig.savefig(os.path.join(figs, "fig1_main.png"), dpi=150)

    # 图2：混淆衰减曲线
    fig, ax = plt.subplots(figsize=(9, 4.8))
    markers = ["o", "s", "^", "D", "v"]
    for i, (m, row) in enumerate(decay.items()):
        ax.plot(levels, [row.get(l, float("nan")) for l in levels],
                marker=markers[i % 5], label=m)
    ax.set_ylabel("F1 / 召回"); ax.set_ylim(0, 1.05)
    ax.set_title("鲁棒性：对抗混淆强度下的性能衰减")
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(os.path.join(figs, "fig2_decay.png"), dpi=150)

    # 图3：攻击家族热度图
    fig, ax = plt.subplots(figsize=(10, 4.2))
    fams = sorted({k for row in fam.values() for k in row})
    mat = [[fam[m].get(f, np.nan) for f in fams] for m in fam]
    im = ax.imshow(mat, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(fams))); ax.set_xticklabels(fams, fontsize=8, rotation=20)
    ax.set_yticks(range(len(fam))); ax.set_yticklabels(list(fam.keys()), fontsize=9)
    for i in range(len(fam)):
        for j in range(len(fams)):
            if not np.isnan(mat[i][j]):
                ax.text(j, i, f"{mat[i][j]:.2f}", ha="center", va="center", fontsize=8)
    ax.set_title("各方法 × 攻击家族 F1（越绿越能抓）")
    fig.colorbar(im); fig.tight_layout()
    fig.savefig(os.path.join(figs, "fig3_family.png"), dpi=150)

    # 图4：LOFO 跨家族泛化
    ms4 = list(next(iter(lofo.values())).keys())
    fig, ax = plt.subplots(figsize=(10, 4.6))
    x = np.arange(len(fams_all)); w = 0.16
    for i, m in enumerate(ms4):
        vals = [lofo[f][m] for f in fams_all]
        ax.bar(x + (i - len(ms4)/2 + 0.5) * w, vals, w, label=m)
        for xi, v in zip(x + (i - len(ms4)/2 + 0.5) * w, vals):
            ax.text(xi, v + 0.02, f"{v:.2f}", ha="center", fontsize=7)
    ax.set_xticks(x); ax.set_xticklabels([f.replace("_", "\n") for f in fams_all], fontsize=8)
    ax.set_ylabel("留出该家族后的 F1"); ax.set_ylim(0, 1.1); ax.legend(fontsize=8)
    ax.set_title("跨攻击家族泛化（LOFO）：训练时从未见过该家族")
    ax.grid(axis="y", alpha=0.3); fig.tight_layout()
    fig.savefig(os.path.join(figs, "fig4_lofo.png"), dpi=150)

    print(json.dumps(payload, ensure_ascii=False, indent=1))
    print("\n图表输出：figures/fig1_main.png fig2_decay.png fig3_family.png")

if __name__ == "__main__":
    main()
