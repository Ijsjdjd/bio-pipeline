"""Single entry point. Default uses imported cache; fresh S3 must be explicitly bounded."""
import argparse,sys,os,pathlib,json
R=pathlib.Path(__file__).resolve().parent
sys.path[:0]=[str(R/'extras'),str(R/'deps'),str(R/'frozen/pipeline')]
def main():
 import fcntl
 lock=(R/'workflow.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 p=argparse.ArgumentParser();p.add_argument('--s3-prefix');p.add_argument('--max-files',type=int);p.add_argument('--max-download-gb',type=float);p.add_argument('--prepare-only',action='store_true');a=p.parse_args();cfg=json.loads((R/'config.json').read_text())
 import intake
 if a.s3_prefix:
  assert a.max_files and a.max_download_gb,'Fresh downloads need explicit file and byte caps'
  assert not (R/'production_manifest.json').exists(),'Use a fresh run directory for a new cohort'
  assert (R/'eval_metadata.jsonl').exists(),'Require frozen eval exclusions'
  key=sys.stdin.readline().strip();assert key
  os.environ.update(BIO_API_ROUTE='direct',BIO_API_STREAM_MODELS='',BIO_BATCH_BUDGET_DB=str(R/'budget.sqlite'),BIO_BATCH_BUDGET_CNY=str(cfg['budget_cny']))
  import runner;runner.admission=runner.Admission(cfg['api_concurrency'],cfg['requests_per_minute'])
  from acquire import acquire
  acquire(a.s3_prefix,a.max_files,int(a.max_download_gb*1e9),key)
 else:key=None
 intake.main()
 if not a.prepare_only:
  import runner
  runner.main(key)
if __name__=='__main__':main()
