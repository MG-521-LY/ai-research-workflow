"""Routing and real HTTP/file readback contracts, without a fake plugin save."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'InstSCI'))
from instsci.acquisition import acquire, decision, fallback_route
from instsci.connector_first import ZoteroReadback, extension_directory, matching_parents, try_connector

TITLE = 'Synthetic Connector readback contract fixture'
REQUEST = {'doi': '10.1234/connector.fixture', 'title': TITLE}


class ConnectorFirstTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)

    def test_explicit_failure_matrix(self):
        self.assertEqual(decision({'status':'verified_pdf','row':{'verified_match':True}}), 'reuse_verified_pdf')
        self.assertEqual(decision({'status':'metadata_only','fallback_allowed':True,'zotero_item_key':'PARENT01'}),
                         'fallback_attachment_to_existing_parent')
        for status in ['connector_pending','readback_uncertain','ambiguous_parent','quota_state_error']:
            self.assertEqual(decision({'status':status,'fallback_allowed':False}), 'pause_no_duplicate_save')
        self.assertEqual(decision({'status':'connector_unavailable','fallback_allowed':True}), 'fallback_download')

    def test_every_source_has_same_first_stage_with_source_specific_fallback(self):
        for source in ['openalex','crossref','semantic_scholar','arxiv','publisher','author_repository']:
            self.assertEqual(fallback_route({**REQUEST,'source':source}), 'doi')
        for portal in ['cnki','wanfang']:
            self.assertEqual(fallback_route({'portal':portal,'title':TITLE}), portal)
        self.assertEqual(fallback_route(REQUEST,'library'), 'library')

    def test_parent_matching_uses_exact_doi_or_title_and_first_author(self):
        parent={'key':'PARENT01','data':{'DOI':REQUEST['doi'],'title':TITLE,'itemType':'journalArticle'}}
        self.assertEqual(matching_parents([parent],REQUEST),[parent])
        self.assertFalse(matching_parents([parent],{**REQUEST,'doi':'10.1234/other'}))
        chinese={'key':'PARENT02','data':{'title':'推进器测试论文','creators':[{'lastName':'张','firstName':'三'},{'name':'李四'}]}}
        self.assertEqual(matching_parents([chinese],{'title':'推进器测试论文','first_author':'张三'}),[chinese])
        self.assertFalse(matching_parents([chinese],{'title':'推进器测试论文','first_author':'李四'}))
        self.assertFalse(matching_parents([chinese],{'title':'推进器测试论文'}))

    def test_readback_refuses_remote_endpoints_and_auth_urls(self):
        for base in ['https://example.com','http://user:secret@127.0.0.1:23119','http://127.0.0.1:23119/?token=secret']:
            with self.subTest(base=base),self.assertRaises(ValueError):
                ZoteroReadback(base)

    def test_extension_detection_does_not_treat_generic_browser_as_plugin(self):
        p=self.work/'extension';p.mkdir()
        (p/'manifest.json').write_text(json.dumps({'name':'Some other extension','manifest_version':3}),encoding='utf-8')
        self.assertIsNone(extension_directory(str(p)))

    def test_first_request_uncertain_save_is_not_replayed_on_resume(self):
        output=self.work/'run';output.mkdir()
        (output/'acquisition_state.json').write_text(json.dumps({'records':[REQUEST],'fallback':'auto',
            'institution':'','library_config':'','results':[],'active_connector_request':0}),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'may still be active'):
            acquire([REQUEST],output,resume=True)
        self.assertFalse((output/'.connector_acquire.lock').exists())

    def test_no_commit_queries_library_but_never_starts_browser_or_plugin_save(self):
        extension=self.work/'extension';extension.mkdir()
        (extension/'manifest.json').write_text(json.dumps({'name':'Zotero Connector','manifest_version':3}),encoding='utf-8')
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(b'[]')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        with patch.dict(os.environ,{'ZOTERO_LOCAL_API_URL':f'http://127.0.0.1:{server.server_port}',
                                     'INSTSCI_CONNECTOR_READ_ONLY':'1'}):
            result=try_connector(REQUEST,self.work/'read_only',
                                 config=SimpleNamespace(zotero_connector_extension=str(extension)))
        self.assertEqual(result['status'],'connector_save_disabled')
        self.assertTrue(result['fallback_allowed'])
        self.assertEqual(len(requests),1)
        self.assertFalse((self.work/'read_only').exists())

    def test_real_local_api_file_redirect_readback_and_duplicate_parent_pause(self):
        with pymupdf.open() as doc:
            page=doc.new_page();page.insert_text((40,50),TITLE+'\nAuthor One\n'+REQUEST['doi']+'\nAbstract\nContract fixture.',fontsize=10)
            raw=doc.tobytes()
        stored=self.work/'ATTACH01'/'article.pdf';stored.parent.mkdir();stored.write_bytes(raw)
        parent={'key':'PARENT01','data':{'itemType':'journalArticle','title':TITLE,'DOI':REQUEST['doi']}}
        child={'key':'ATTACH01','data':{'itemType':'attachment','parentItem':'PARENT01','contentType':'application/pdf',
                                      'linkMode':'imported_file','filename':'article.pdf'}}
        shared={'parents':[parent],'redirect':stored.as_uri()}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path.endswith('/file'):
                    self.send_response(302);self.send_header('Location',shared['redirect']);self.end_headers();return
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(json.dumps([child] if '/children' in self.path else shared['parents']).encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        reader=ZoteroReadback(f'http://127.0.0.1:{server.server_port}')
        result=reader.inspect(REQUEST,self.work/'download')
        self.assertEqual(result['status'],'verified_pdf')
        self.assertEqual(result['row']['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['row']['zotero_item_key'],'PARENT01')
        self.assertEqual(result['row']['zotero_attachment_key'],'ATTACH01')
        self.assertEqual(result['row']['result_evidence'],'local_verified_reuse')
        shared['parents']=[parent,parent]
        self.assertEqual(reader.inspect(REQUEST,self.work/'other')['status'],'ambiguous_parent')
        shared['parents']=[parent];shared['redirect']=(self.work/'unrelated.pdf').as_uri()
        uncertain=reader.inspect(REQUEST,self.work/'bad')
        self.assertEqual(uncertain['status'],'readback_uncertain')
        self.assertFalse(uncertain['fallback_allowed'])
        self.assertEqual(uncertain['zotero_item_key'],'PARENT01')


if __name__=='__main__':unittest.main()
