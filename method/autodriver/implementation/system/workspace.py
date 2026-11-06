"""Register only prepared public task inputs and method-derived materials."""
from pathlib import Path
import json

import yaml

from .protocol.assembler import digest,read_pinned,write_private_text,virtual_parts
from .evidence import Evidence,extract_pdf


class Workspace:
    def __init__(self,root):
        self.root=Path(root)
        self.manifest=json.loads((self.root/'materials.json').read_text())
        self.task=yaml.safe_load(self.read_original('task.yaml').decode('utf8'))
        if self.task.get('schema')!='autodriver.public-task/v1' or self.task.get('condition')!='SD':
            raise ValueError('new autodriver system admits prepared SD inputs only')
        if self.manifest.get('task_id')!=self.task['task_id'] or self.manifest.get('condition')!='SD':
            raise ValueError('prepared public manifest/task mismatch')
        self.entries={}
        self.roles={}
        self.generated=self.root/'output/autodriver'
        self.generated.mkdir(parents=True,exist_ok=True)

    def read_original(self,path):
        virtual_parts(path)
        entry=self.manifest['files'].get(path)
        if entry is None:raise ValueError('file is not in prepared public input manifest')
        return read_pinned(self.root,{'id':'prepared-input','path':path,'sha256':entry['sha256']})

    def add_original(self,id,path,kind,role,*,os_role='shared'):
        data=self.read_original(path)
        self._insert(id,path,kind,role,data,(),os_role,origin='prepared manifest '+self.manifest['identity'])
        return id

    def _insert(self,id,path,kind,role,data,parents,os_role,*,producer=None,origin=''):
        if id in self.entries:raise ValueError('material ID cannot be overwritten')
        if set(parents)-self.entries.keys():raise ValueError('derived material has missing parents')
        entry={'id':id,'path':path,'sha256':digest(data),'kind':kind,'approved':True,'parents':list(parents),
               'os_role':os_role,'origin_locator':origin}
        if producer:entry['producer']=producer
        self.entries[id]=entry;self.roles[id]=role

    def add_derived(self,id,filename,kind,role,text,parents,*,os_role='shared',producer=None):
        virtual_parts(filename)
        path=self.generated/filename
        if path.exists() or path.is_symlink():raise ValueError('derived artifact already exists; create a new version')
        write_private_text(path,text)
        self._insert(id,path.relative_to(self.root).as_posix(),kind,role,text.encode(),parents,os_role,producer=producer)
        return id

    def bootstrap(self,*,semantic_guide=None):
        # Public integration headers/components also carry necessary product
        # and acquisition requirements. Preserve their exact text and identity
        # in the task projection, so DBC does not lose those approved inputs.
        integration=[];support_ids=[]
        for i,path in enumerate(self.task['target']['interface_files']):
            id=self.add_original('support.'+str(i),path,'public_target_support','target_support',os_role='target')
            support_ids.append(id)
            integration.append({'material_id':id,'path':path,'sha256':self.entries[id]['sha256'],
                                'text':self.read_original(path).decode('utf8')})
        native_interface=dict(self.task['target']['interface'])
        if 'public_integration_evidence' in native_interface:raise ValueError('reserved integration-evidence projection field')
        native_interface['public_integration_evidence']=integration
        projection={'task_id':self.task['task_id'],'sensor':self.task['sensor'],'input_condition':'SD',
                    'source_os':self.task['source']['os'],'target_os':self.task['target']['os'],
                    'requirements':self.task['requirements'],'native_interface':native_interface,
                    'editable_paths':['target/'+p for p in self.task['submission']['editable_paths']]}
        self.add_derived('task','task.json','public_task_projection','task_context',json.dumps(projection,ensure_ascii=False,indent=2)+'\n',support_ids)
        spec=Path(__file__).parent/'protocol/FORMAT_GUIDE.md'
        self.add_derived('method','method.md','method_specification','method_specification',
                         spec.read_text() if semantic_guide is None else semantic_guide,())
        for i,item in enumerate(self.task['source']['selected_files']):
            self.add_original('source.'+str(i),item['path'],'device_source','source_evidence',os_role='source')
        for i,path in enumerate(self.task['source']['configuration_files']):
            self.add_original('configuration.'+str(i),path,'source_configuration','source_configuration',os_role='source')
        pdfs=[path for path in self.manifest['files'] if path.startswith('documents/') and path.lower().endswith('.pdf')]
        for i,path in enumerate(sorted(pdfs)):
            id=self.add_original('pdf.'+str(i),path,'device_document','document_evidence')
            pages=extract_pdf(self.evidence(),id)
            text='\n\n'.join(f"[Original PDF page {p['page']}]\n"+p['text'] for p in pages['pages'])
            self.add_derived('document.'+str(i),'document-'+str(i)+'.txt','document_extract','document_evidence',text,(id,))
            write_private_text(self.generated/('document-'+str(i)+'.coordinates.json'),json.dumps(pages,ensure_ascii=False,indent=2)+'\n')
        return self

    def evidence(self):
        return Evidence(self.root,{'materials':list(self.entries.values())})

    def macro_configuration(self):
        result={};found=False
        for path in self.task['source']['configuration_files']:
            if Path(path).suffix not in {'.yaml','.yml'}:continue
            values=yaml.safe_load(self.read_original(path))
            if not isinstance(values,dict):continue
            for key,value in values.items():
                if isinstance(key,str) and key.isidentifier() and type(value) in {bool,int}:
                    if key in result and result[key]!=value:raise ValueError('conflicting frozen macro selections')
                    result[key]=value;found=True
        return result if found else None

    def selections(self,roles):
        return [{'role':self.roles[id],'material_id':id} for id in self.entries if self.roles[id] in roles
                and not self.entries[id]['path'].lower().endswith('.pdf')]

    def source_membership(self):
        from .source_analysis import cmake_source_membership
        records=[]
        for id,entry in self.entries.items():
            if entry['kind']!='device_source' or Path(entry['path']).name!='CMakeLists.txt':continue
            for record in cmake_source_membership(self.evidence().read(id).decode(),str(Path(entry['path']).parent),self.macro_configuration() or {}):
                records.append(dict(record,material_id=id,sha256=entry['sha256']))
        return records
