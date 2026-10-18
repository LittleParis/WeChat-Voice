import hashlib
import json
from pathlib import Path
import time
import zipfile

import miniaudio
import pytest

from test_voice_library import library, serve, browser, post
from mobile_package import create_package, PackageCancelled


def test_bundle_preserves_identity_metadata_and_audio(library,tmp_path):
    path=tmp_path/'离线语音.voicepack.zip'
    result=create_package(library,path,library.records)
    assert result['exported']==4
    with zipfile.ZipFile(path) as bundle:
        manifest=json.loads(bundle.read('manifest.json'))
        assert manifest['format']=='voicekeep' and manifest['version']==1
        assert len(manifest['chats'])==2 and len(manifest['voices'])==4
        assert len(bundle.namelist())==5
        assert len({row['sender_id'] for row in manifest['voices']})==4
        for row in manifest['voices']:
            assert row['file']=='audio/'+row['id']+'.mp3'
            encoded=bundle.read(row['file'])
            assert hashlib.sha256(encoded).hexdigest()==row['sha256']
            assert .95<len(miniaudio.decode(encoded,nchannels=1,sample_rate=24000).samples)/24000<1.2
        assert not any('database' in row or 'key' in row for row in manifest['voices'])


def test_bundle_cancellation_and_encoding_failure_never_leave_complete_zip(library,tmp_path,monkeypatch):
    path=tmp_path/'cancel.zip'
    with pytest.raises(PackageCancelled):
        create_package(library,path,library.records,should_cancel=lambda:True)
    assert not path.exists() and not list(tmp_path.glob('package-*'))
    def fail(_): raise RuntimeError('broken input')
    monkeypatch.setattr(library,'prepare',fail)
    with pytest.raises(RuntimeError): create_package(library,path,library.records)
    assert not path.exists()


def test_existing_package_not_overwritten_and_empty_refused(library,tmp_path):
    path=tmp_path/'existing.zip';path.write_bytes(b'existing')
    with pytest.raises(FileExistsError):create_package(library,path,library.records)
    assert path.read_bytes()==b'existing'
    with pytest.raises(ValueError):create_package(library,tmp_path/'empty.zip',[])


def test_web_mobile_package_obeys_same_sender_and_date_filters(library):
    group=next(chat for chat in library.chats if chat['kind']=='group')
    selected=next(row for row in library.records if row['chat_id']==group['id'] and row['sender_id']!='unknown')
    with serve(library) as (_,base):
        opener,token=browser(base)
        job=post(opener,base,'/api/export',{'chat':group['id'],'sender':selected['sender_id'],'since':selected['date'],'until':selected['date'],'mode':'mobile'},token)
        deadline=time.monotonic()+10
        while job['state'] not in ('done','error') and time.monotonic()<deadline:
            time.sleep(.03)
            job=json.load(opener.open(base+'/api/jobs/'+job['id']))
        assert job['state']=='done' and job['exported']==1 and job['mode']=='mobile'
        with zipfile.ZipFile(next(Path(job['output']).glob('*.zip'))) as bundle:
            manifest=json.loads(bundle.read('manifest.json'))
            assert [row['id'] for row in manifest['voices']]==[selected['id']]
