"""Weighted ranking, 2000 image-cluster bootstrap draws and two-fold image-disjoint fusion."""
from __future__ import annotations
import argparse
from exp6j_common import *

SUBSETS = {'main': ('hit','fp','lowthr_pos','lowthr_neg'), 'detector-positive': ('hit','fp'),
           'low-threshold': ('lowthr_pos','lowthr_neg'), 'recall-side': ('miss','unchanged')}


def point(y, score, weight):
    import numpy as np
    from sklearn.metrics import roc_auc_score, average_precision_score
    active = weight > 0
    if len(np.unique(y[active])) != 2:
        return {'auc': None, 'ap': None}
    return dict(auc=float(roc_auc_score(y,score,sample_weight=weight)),
                ap=float(average_precision_score(y,score,sample_weight=weight)))


def metric(rows, score, iterations=2000, seed=SEED, image_universe=None):
    import numpy as np
    y = np.asarray([r['label'] for r in rows])
    w = np.asarray([r['weight'] for r in rows],float)
    score = np.asarray(score,float)
    if score.shape != y.shape or not np.isfinite(score).all() or not np.isfinite(w).all() or (w <= 0).any():
        raise ValueError('Invalid score/weight arrays')
    result = dict(n=len(y), positives=int(y.sum()), weighted_positive_rate=float(np.average(y,weights=w)) if len(y) else None,
                  **point(y,score,w))
    groups = sorted(image_universe if image_universe is not None else {r['image'] for r in rows})
    lookup = {name:i for i,name in enumerate(groups)}
    ids = np.array([lookup[r['image']] for r in rows],int)
    rng = np.random.default_rng(seed)
    values = {'auc': [], 'ap': []}
    for _ in range(iterations):
        counts = rng.multinomial(len(groups),np.full(len(groups),1/len(groups))) if groups else np.array([])
        estimate = point(y,score,w*counts[ids])
        for name in values:
            if estimate[name] is not None:
                values[name].append(estimate[name])
    for name, items in values.items():
        result[name+'_ci95'] = np.percentile(items,[2.5,97.5]).tolist() if items else None
    result.update(bootstrap_draws=iterations, valid_bootstrap_draws=len(values['auc']),
                  bootstrap='image clusters, inclusion weights fixed', image_clusters=len(groups))
    return result


def rank_scores(pred):
    import numpy as np
    mass = [r.get('yes_no_mass') for r in pred]
    if any(v is None for v in mass):
        raise ValueError('Two-token mass missing')
    low = any(v < .9 for v in mass)
    key = 'logit_diff' if low else 'p_yes'
    if low and any(r.get('logit_diff') is None for r in pred):
        # Historical exp3 has probabilities but no logits; recover finite log odds when possible.
        if any(not 0 < r['p_yes'] < 1 for r in pred):
            raise ValueError('Low-mass historical scores include saturated probabilities; cannot recover logits')
        scores = np.log([r['p_yes']/(1-r['p_yes']) for r in pred])
        key = 'recovered_log_odds'
    else:
        scores = np.asarray([r[key] for r in pred],float)
    return scores, dict(ranking_readout=key, mass_mean=float(np.mean(mass)), mass_min=float(min(mass)),
                        mass_below_0_9_fraction=float(np.mean(np.asarray(mass)<.9)))


def split_images(images,seed=SEED):
    import numpy as np
    shuffled = np.random.default_rng(seed).permutation(sorted(set(images))).tolist()
    middle = len(shuffled)//2
    return [shuffled[:middle],shuffled[middle:]]


def best_threshold(y,score,weight):
    import numpy as np
    from sklearn.metrics import precision_recall_curve
    precision,recall,threshold = precision_recall_curve(y,score,sample_weight=weight)
    f1 = 2*precision[:-1]*recall[:-1]/np.maximum(precision[:-1]+recall[:-1],1e-15)
    # sklearn's thresholds classify >=; error panels use the same convention.
    return float(threshold[int(np.argmax(f1))])


def crossfit(rows, pred, halves, iterations):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    x = np.array([[p['p_yes'],r['M1']] for r,p in zip(rows,pred)])
    y = np.array([r['label'] for r in rows])
    w = np.array([r['weight'] for r in rows])
    oof = np.full(len(rows),np.nan)
    detector_pred = np.zeros(len(rows),bool)
    folds = []
    for fold,test_images in enumerate(halves):
        heldout = set(test_images)
        test = np.array([r['image'] in heldout for r in rows])
        train = ~test
        if len(np.unique(y[train])) != 2 or len(np.unique(y[test])) != 2:
            raise ValueError('Each image half must contain both classes for fusion')
        fit = LogisticRegression(C=1.0,solver='lbfgs',max_iter=1000,random_state=SEED)
        fit.fit(x[train],y[train],sample_weight=w[train]/w[train].mean())
        if int(fit.n_iter_.max()) >= 1000:
            raise RuntimeError('Fusion logistic regression did not converge')
        oof[test] = fit.predict_proba(x[test])[:,1]
        d = np.array([r['D'] for r in rows])
        threshold = best_threshold(y[train],d[train],w[train])
        detector_pred[test] = d[test] >= threshold
        fused = point(y[test],oof[test],w[test])
        baseline = point(y[test],x[test,1],w[test])
        folds.append(dict(fold=fold,train_images=halves[1-fold],test_images=test_images,
             train_samples=int(train.sum()),test_samples=int(test.sum()),
             fusion=fused,M1=baseline,ap_gain=fused['ap']-baseline['ap'],
             detector_threshold=threshold,coefficients=fit.coef_.tolist(),intercept=fit.intercept_.tolist()))
    evaluation = metric(rows,oof,iterations,image_universe=halves[0]+halves[1])
    evaluation.update(folds=folds,fold_mean_auc=float(np.mean([f['fusion']['auc'] for f in folds])),
        fold_mean_ap=float(np.mean([f['fusion']['ap'] for f in folds])),
        M1_fold_mean_ap=float(np.mean([f['M1']['ap'] for f in folds])),
        fold_mean_ap_gain=float(np.mean([f['ap_gain'] for f in folds])),
        ci_scope='Fixed out-of-fold predictions; logistic regression is not refitted within bootstrap')
    return evaluation, oof, detector_pred


def evaluate_step1(args,dataset):
    import numpy as np
    rows = samples(args.out,'step1',dataset)
    methods = {'D': ([r['det_mean_prob'] for r in rows],None)}
    for tag in ('qwen35_4b','qwen37plus'):
        path = BASE / 'exp3' / dataset / f'mllm_{tag}.jsonl'
        old = indexed(read_jsonl(path))
        if set(old) != set(indexed(rows)):
            raise ValueError(f'{path}: historical score coverage differs')
        pred = [old[r['cid']] for r in rows]
        score,info = rank_scores(pred)
        methods[tag] = (score,(pred,info))
    for name in args.models:
        folder = prediction_dir(args.out,'step1',dataset,name,'near',args.packing)
        pred = predictions(folder,rows)
        score,info = rank_scores(pred)
        methods[name] = (score,(pred,info))
    result = {}
    for name,(score,extra) in methods.items():
        entry = {'subsets':{}}
        if extra:
            pred,info = extra
            entry.update(info,yes_rate=float(np.mean([p['p_yes']>.5 for p in pred])))
        for subset,sources in SUBSETS.items():
            ids = [i for i,r in enumerate(rows) if r['source'] in sources]
            chosen = [rows[i] for i in ids]
            m = metric(chosen,np.asarray(score)[ids],args.bootstrap,
                       image_universe=sorted({r['image'] for r in rows}))
            if extra and ids:
                m['yes_rate'] = float(np.mean([pred[i]['p_yes']>.5 for i in ids]))
            entry['subsets'][subset] = m
        result[name] = entry
    return result


def evaluate_step2(args,dataset):
    import numpy as np
    rows = samples(args.out,'step2',dataset)
    info = read_json(sample_path(args.out,'step2',dataset).with_suffix('.meta.json'))
    halves = split_images(info['images'])
    definitions = {
        'cell2_vs_negative': lambda r:r['label']==0 or r['majority']==2,
        'all_positive_vs_negative': lambda r:True,
        'cell2_vs_hard_negative': lambda r:(r['label']==1 and r['majority']==2) or r['hard_negative'],
    }
    result = {'groups':{},'image_halves':halves}
    cached_predictions = {(name,view): predictions(
        prediction_dir(args.out,'step2',dataset,name,view,args.packing),rows)
        for name in args.models for view in args.views}
    for group,keep in definitions.items():
        ids = [i for i,r in enumerate(rows) if keep(r)]
        selected = [rows[i] for i in ids]
        entry = {name: metric(selected,[r[name] for r in selected],args.bootstrap,
                             image_universe=info['images']) for name in ('D','M1')}
        for name in args.models:
            for view in args.views:
                pred = [cached_predictions[(name,view)][i] for i in ids]
                score,readout_info = rank_scores(pred)
                m = metric(selected,score,args.bootstrap,image_universe=info['images'])
                w = np.array([r['weight'] for r in selected])
                m.update(readout_info,yes_rate=float(np.average([p['p_yes']>.5 for p in pred],weights=w)),
                         yes_rate_unweighted=float(np.mean([p['p_yes']>.5 for p in pred])),
                         mass_mean_weighted=float(np.average([p['yes_no_mass'] for p in pred],weights=w)),
                         delta_auc_D=m['auc']-entry['D']['auc'] if m['auc'] is not None else None,
                         delta_auc_M1=m['auc']-entry['M1']['auc'] if m['auc'] is not None else None)
                fusion,oof,detector_pred = crossfit(selected,pred,halves,args.bootstrap)
                m['fusion'] = fusion
                entry[f'{name}_{view}_{args.packing}'] = m
                if group == 'all_positive_vs_negative':
                    decisions = [dict(cid=r['cid'],p_yes=p['p_yes'],D=r['D'],label=r['label'],
                        mllm_correct=(p['p_yes']>.5)==bool(r['label']),
                        detector_correct=bool(d)==bool(r['label']),fusion=float(f))
                        for r,p,d,f in zip(selected,pred,detector_pred,oof)]
                    write_jsonl(args.out / 'evaluation/decisions' / dataset /
                                f'{name}_{view}_{args.packing}.jsonl',decisions)
        result['groups'][group] = entry
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('step1','step2'),required=True)
    p.add_argument('--models',nargs='+',choices=MODEL_PATHS,required=True)
    p.add_argument('--datasets',nargs='+',choices=('levir','whu','sysu','second'))
    p.add_argument('--views',nargs='+',choices=('near','context'),default=['near','context'])
    p.add_argument('--packing',choices=('multi','sheet'),default='multi')
    p.add_argument('--out',type=Path,default=RUN)
    p.add_argument('--bootstrap',type=int,default=2000)
    args = p.parse_args()
    args.out = local(args.out)
    if args.bootstrap < 1:
        p.error('--bootstrap must be positive')
    datasets = args.datasets or (['levir','whu','sysu','second'] if args.stage=='step1' else ['sysu','second'])
    if args.stage == 'step2' and not set(datasets) <= {'sysu','second'}:
        p.error('step2 only supports sysu/second')
    for dataset in datasets:
        result = (evaluate_step1 if args.stage=='step1' else evaluate_step2)(args,dataset)
        result = dict(stage=args.stage,dataset=dataset,packing=args.packing,models=args.models,
                      bootstrap=args.bootstrap,**result)
        # One file per exact method set: additional sheet comparisons never overwrite main results.
        dest = args.out / 'evaluation' / f'{args.stage}_{dataset}_{args.packing}_{"-".join(args.models)}.json'
        write_json(dest,result)
        print(dest,flush=True)

if __name__ == '__main__':
    main()
