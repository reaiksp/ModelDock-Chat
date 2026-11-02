"""A CLI exit must be followed by local metadata and real loading verification."""
import pytest
from chatdock.network import model_key, verify_local_model, Task, Cancelled

TAG='hf.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF:Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf'

class Client:
 def __init__(self):
  self.calls=[]; self.rows=[{'name':TAG.lower()}];self.info={'model_info':{'qwen35.context_length':262144}};self.loaded=True
 def request(self,path,body=None,task=None,timeout=180):
  if task:task.check()
  self.calls.append((path,body))
  if path=='/api/tags':return {'models':self.rows}
  if path=='/api/show':return self.info
  if path=='/api/generate':return {'done':self.loaded}
  raise AssertionError(path)

def test_hf_filename_normalization_and_real_loading():
 c=Client();actual=verify_local_model(c,TAG,16384,Task())
 assert actual==TAG.lower()
 assert c.calls[-1][1]['options']['num_ctx']==16384
 assert c.calls[-1][1]['stream'] is False
 assert model_key('huggingface.co/X/Y:File.GGUF')==model_key('hf.co/x/y:file.gguf')
 assert model_key('qwen3')==model_key('qwen3:latest')

@pytest.mark.parametrize('mode',['missing','remote','context','incomplete'])
def test_invalid_local_deployment_does_not_pass(mode):
 c=Client()
 if mode=='missing':c.rows=[]
 if mode=='remote':c.rows[0]['remote_host']='https://example.com'
 if mode=='context':c.info={'model_info':{'qwen35.context_length':4096}}
 if mode=='incomplete':c.loaded=False
 with pytest.raises(RuntimeError):verify_local_model(c,TAG,16384,Task())

def test_cancelled_deployment_makes_no_requests():
 c=Client();task=Task();task.cancel()
 with pytest.raises(Cancelled):verify_local_model(c,TAG,4096,task)
 assert c.calls==[]
