import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('engine',Path(__file__).with_name('loop.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.exp=Path(self.temp.name);(self.exp/'jobs').mkdir()
        for i in range(4): (self.exp/'jobs'/f'{i}.sbatch').write_text('#!/bin/sh\ntrue\n')
        self.cfg=dict(metric=['python3','metric.py'],partition='gre',notify=False,
                      stop=dict(no_gain_rounds=2,min_gain=1.,budget_cny=10))
        self.config();self.calls=[];self.next_id=100;self.states={};self.metrics=[]
        self.fail_hook=False;self.fail_qq=False;self.fail_push=False
        self.mock=patch.object(m,'run',self.fake);self.mock.start();self.addCleanup(self.mock.stop)
        self.output=contextlib.redirect_stdout(io.StringIO());self.output.__enter__();self.addCleanup(self.output.__exit__,None,None,None)
    def config(self): (self.exp/'exp.json').write_text(json.dumps(self.cfg))
    def fake(self,args,**kwargs):
        self.calls.append(args)
        if args[0]=='scontrol': return 0,'Slurmctld(primary) is UP',''
        if args[0]=='sbatch':
            if self.fail_hook and '/hooks/' in args[-1]: return 1,'','hook rejected'
            self.next_id+=1;return 0,str(self.next_id),''
        if args[0]=='sacct': return 0,self.states.get(args[2],'COMPLETED')+'|0:0',''
        if args==self.cfg['metric']:
            item=self.metrics.pop(0)
            return 0,json.dumps(item),''
        if args[0]==m.QQ: return (1,'','mail failed') if self.fail_qq else (0,'sent','')
        raise AssertionError(args)
    def state(self): return m.load(self.exp)
    def end(self,primary,cost=0):
        self.metrics.append(dict(primary=primary,cost_cny=cost));s=self.state();m.advance(self.exp,s['job_id'])
    def test_three_round_chain_and_positive_small_gains_stop(self):
        m.init(self.exp);self.end(10);self.end(10.4);self.end(10.8)
        s=self.state();self.assertEqual(s['rounds'],3);self.assertEqual(s['no_gain'],2)
        self.assertEqual(s['status'],'done');self.assertEqual(len(s['queue']),1)
        parents=[c for c in self.calls if c[0]=='sbatch' and '/jobs/' in c[-1]]
        hooks=[c for c in self.calls if c[0]=='sbatch' and '/hooks/' in c[-1]]
        self.assertEqual(len(parents),3);self.assertEqual(len(hooks),3)
        self.assertTrue(all(any(a.startswith('--dependency=afterany:') for a in c) for c in hooks))
    def test_duplicate_hook_and_reinit_do_not_duplicate_jobs(self):
        m.init(self.exp);old=self.state()['job_id'];self.end(10)
        before=len(self.calls);m.advance(self.exp,old);self.assertEqual(len(self.calls),before)
        with self.assertRaises(ValueError):m.init(self.exp)
    def test_tick_repairs_missing_or_failed_hook_without_experiment(self):
        self.fail_hook=True;m.init(self.exp);s=self.state();self.assertIsNone(s['hook_id'])
        self.fail_hook=False;start=len(self.calls);m.tick(self.exp)
        self.assertTrue(all('/hooks/' in c[-1] for c in self.calls[start:] if c[0]=='sbatch'))
        self.assertEqual(self.state()['rounds'],0)
        failed_hook=self.state()['hook_id'];self.states[failed_hook]='FAILED';m.tick(self.exp)
        self.assertEqual(self.state()['rounds'],0)
        self.assertEqual(self.state()['rounds_submitted'],1)
        queried=[c[2] for c in self.calls if c[0]=='sacct']
        self.assertTrue(all(x==failed_hook for x in queried),queried)
    def test_running_hook_untouched(self):
        m.init(self.exp);self.states[self.state()['hook_id']]='RUNNING';start=len(self.calls)
        m.tick(self.exp);self.assertFalse(any(c[0]=='sbatch' for c in self.calls[start:]))
    def test_estimate_does_not_block_submission(self):
        self.cfg['round_cost_cny']={'0.sbatch':11};self.config();m.init(self.exp)
        self.assertEqual(self.state()['rounds_submitted'],1)
        self.assertTrue(any(c[0]=='sbatch' for c in self.calls))
    def test_actual_budget_stops(self):
        m.init(self.exp);self.end(10,10);self.assertEqual(self.state()['rounds_submitted'],1)
    def test_missing_metric_stops(self):
        m.init(self.exp);self.metrics.append({});m.advance(self.exp,self.state()['job_id'])
        self.assertEqual(self.state()['status'],'failed');self.assertEqual(self.state()['rounds_submitted'],1)
    def test_failed_parent_stops_and_records_cost(self):
        m.init(self.exp);self.states[self.state()['job_id']]='TIMEOUT';self.end(2,.3)
        self.assertEqual(self.state()['status'],'failed');self.assertEqual(self.state()['cost_cny'],.3)
    def test_add_after_queue_drained_uses_start_hook(self):
        self.cfg['jobs_glob']='jobs/0.sbatch';self.config();m.init(self.exp);self.end(10)
        m.finalize(self.exp);self.assertTrue(self.state()['finalized'])
        m.add(self.exp,self.exp/'jobs/1.sbatch');s=self.state()
        self.assertFalse(s['finalized']);self.assertIsNone(s['job_id']);self.assertTrue(s['hook_id'])
        m.advance(self.exp,'start');self.assertEqual(self.state()['rounds_submitted'],2)
    def test_add_does_not_bypass_budget(self):
        m.init(self.exp);self.end(10,10)
        (self.exp/'extra.sbatch').write_text('true')
        with self.assertRaises(ValueError):m.add(self.exp,self.exp/'extra.sbatch')
    def test_recorded_event_recovery_does_not_double_bill(self):
        m.init(self.exp);s=self.state();self.metrics.append(dict(primary=5,cost_cny=1))
        m.record(self.exp,self.cfg,s,'COMPLETED','0:0')
        m.advance(self.exp,s['job_id']);self.assertEqual(self.state()['cost_cny'],1)
        self.assertEqual(self.state()['rounds'],1);self.assertEqual(self.state()['rounds_submitted'],2)
    def test_qq_failure_remains_pending_then_retries(self):
        self.cfg['notify']=True;self.cfg['finalize_retry_seconds']=0;self.config()
        m.init(self.exp);self.end(10,10);self.fail_qq=True
        self.assertEqual(m.finalize(self.exp),1);self.assertFalse(self.state()['finalized'])
        self.fail_qq=False;m.finalize(self.exp);self.assertTrue(self.state()['finalized'])
        before=len(self.calls);m.finalize(self.exp);self.assertEqual(len(self.calls),before)
    def test_push_failure_is_retryable_without_recommit(self):
        self.cfg['finalize_retry_seconds']=0;self.config();m.init(self.exp);self.end(10,10)
        def git_fail(exp,cfg,s):
            s['git_committed']=True;m.save(exp,s);raise ValueError('push failed')
        with patch.object(m,'git_finalize',git_fail):m.finalize(self.exp)
        self.assertFalse(self.state()['finalized']);self.assertTrue(self.state()['git_committed'])
        with patch.object(m,'git_finalize') as g:m.finalize(self.exp)
        self.assertTrue(self.state()['finalized']);self.assertEqual(g.call_count,1)
    def test_retry_limit_reports_unfinished(self):
        self.cfg.update(notify=True,finalize_retry_seconds=0,max_finalize_attempts=2);self.config()
        m.init(self.exp);self.end(1,10);self.fail_qq=True
        m.finalize(self.exp);m.finalize(self.exp);before=len(self.calls);m.finalize(self.exp)
        self.assertTrue(self.state()['finalize_blocked']);self.assertFalse(self.state()['finalized'])
        self.assertEqual(len(self.calls),before)
    def test_status_never_contacts_slurm(self):
        m.init(self.exp);before=len(self.calls);m.status(self.exp);self.assertEqual(len(self.calls),before)

class GitFinalizationTests(unittest.TestCase):
    def test_push_retry_preserves_unrelated_staged_file(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo=root/'repo';repo.mkdir();exp=root/'exp';exp.mkdir()
            def git(*args):
                return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True).stdout.strip()
            git('init','-b','research');git('config','user.name','Loop Test');git('config','user.email','loop@example.invalid')
            (repo/'initial').write_text('initial');git('add','initial');git('commit','-m','initial')
            (repo/'user.txt').write_text('user change');git('add','user.txt')
            (repo/'result.txt').write_text('experiment result')
            cfg=dict(git=dict(repo=str(repo),branch='research',paths=['result.txt']))
            state=dict(name='test',rounds=3);attempts=[];original=m.run
            def commands(args,**kw):
                if args[0]==m.WGIT:
                    attempts.append(args)
                    return (1,'','network failure') if len(attempts)==1 else (0,'','')
                return original(args,**kw)
            with patch.object(m,'run',commands):
                with self.assertRaises(ValueError):m.git_finalize(exp,cfg,state)
                self.assertTrue(state['git_committed']);self.assertNotIn('git_pushed',state)
                m.git_finalize(exp,cfg,state)
            self.assertEqual(git('rev-list','--count','HEAD'),'2')
            self.assertEqual(git('diff','--cached','--name-only'),'user.txt')
            self.assertTrue(state['git_pushed'])

if __name__=='__main__':unittest.main()
