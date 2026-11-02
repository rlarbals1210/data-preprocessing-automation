"""Build deterministic benchmark matrices and private labels, never change clean CSVs."""
import csv
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

SEED = 20261016
KOREAN_HEADERS = {
    'customer_id':'고객번호','customer_name':'고객명','email':'이메일','phone':'휴대폰','customer_status':'회원상태','marketing_consent':'마케팅동의','joined_at':'가입일시','updated_at':'수정일시',
    'address_id':'주소번호','address_label':'배송지별칭','recipient_name':'받는분','recipient_phone':'수령인연락처','postal_code':'우편번호','address_line1':'기본주소','address_line2':'상세주소','is_default':'기본배송지','created_at':'등록일시',
    'category_id':'분류번호','parent_category_id':'상위분류번호','category_name':'분류명','is_active':'사용여부','product_id':'상품번호','product_name':'상품명','brand_name':'브랜드','description':'상품설명','product_status':'상품상태',
    'sku_id':'옵션코드','option_name':'옵션명','barcode':'바코드','current_price':'판매가','weight_g':'무게_g','variant_status':'판매상태','order_id':'주문번호','ordered_at':'주문일시','order_channel':'주문경로','order_status':'주문상태','delivery_memo':'배송요청','shipping_fee':'배송비','order_total':'주문총액','cancelled_at':'취소일시',
    'order_item_id':'주문상세번호','product_name_snapshot':'주문상품명','option_name_snapshot':'주문옵션명','quantity':'수량','unit_price':'단가','discount_amount':'할인액','line_total':'항목금액','payment_id':'결제번호','payment_method':'결제수단','payment_status':'결제상태','amount':'금액','requested_at':'요청일시','paid_at':'결제일시','failure_code':'실패코드',
    'shipment_id':'출고번호','carrier_code':'택배사','tracking_number':'송장번호','shipment_status':'배송상태','shipped_at':'출고일시','delivered_at':'배송완료일시','shipment_item_id':'출고상세번호','return_id':'반품번호','return_status':'반품상태','received_at':'입고일시','closed_at':'종료일시','return_item_id':'반품상세번호','reason_code':'반품사유코드','reason_detail':'상세사유','item_condition':'물품상태','refund_id':'환불번호','refund_type':'환불유형','refund_status':'환불상태','refunded_at':'환불일시'}

def digest(data):
    return hashlib.sha256(data).hexdigest()

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def build(source, output):
    source, output = Path(source), Path(output)
    schema = json.loads((source/'schema.json').read_text())
    tables = {}
    hashes = {}
    for name in schema['tables']:
        file = source/'data/clean'/f'{name}.csv'
        hashes[name] = digest(file.read_bytes())
        with file.open(encoding='utf-8', newline='') as f:
            tables[name] = list(csv.DictReader(f))
    manifest = json.loads((source/'data/manifest.json').read_text())
    assert all(hashes[k] == v['sha256'] for k,v in manifest['files'].items())
    split_of = {r['customer_id']: ('development' if int(digest((str(SEED)+r['customer_id']).encode())[:8],16)%10<7 else 'evaluation') for r in tables['customers']}
    public_policy = {
        'version':'1.0.0', 'scope':'Synthetic benchmark normalization policy; approve before applying',
        'phone':'한국 휴대전화는 010-XXXX-XXXX. 공백·하이픈·괄호·명시적 +82 국가코드만 정리. 11자리 복원 추측 금지.',
        'null':'nullable 고객 phone/email/주소상세/메모에 한해 빈칸, NULL, 없음, N/A를 null로 통일. phone의 0·미확인은 의미 불확실로 검토. 금액 0은 정상.',
        'money':'KRW 정수. 쉼표·원·₩ 표기는 숫자값 보존하여 정리.',
        'datetime':'시간대 +09:00이 명시되고 연-월-일 순서가 명확한 표현만 ISO로 통일. 07/08/26 같은 모호한 날짜는 검토.',
        'whitespace':'customer_name/product_name/brand_name의 바깥 공백만 제거.',
        'enum':'명시된 스키마 범주와 대소문자만 다른 값은 소문자로 통일.',
        'review':'필수값 결측, 불완전 전화, 수량 0, 미존재 FK, 금액 모순, 중복 PK는 원문 유지·검토 요청. 행 삭제·병합·원값 추측 금지.',
        'mapping':'컬럼명 매핑은 일대일 대응 확인 후 승인. 정답 매핑 파일은 서비스 입력 금지.'}
    save(output/'config/normalization-policy.json',public_policy)
    bundles=[]; private_manifest={'version':'1.0.0','seed':SEED,'clean_hashes':hashes,'schema_sha256':digest((source/'schema.json').read_bytes()),'splits':{}}
    for split in ['development','evaluation']:
        rng = random.Random(SEED+(1 if split=='evaluation' else 0))
        ids={k for k,v in split_of.items() if v==split}
        selected={k:[] for k in tables}
        selected['customers']=[r for r in tables['customers'] if r['customer_id'] in ids]
        selected['customer_addresses']=[r for r in tables['customer_addresses'] if r['customer_id'] in ids]
        for k in ['categories','products','product_variants']: selected[k]=tables[k]
        selected['orders']=[r for r in tables['orders'] if r['customer_id'] in ids]
        order_ids={r['order_id'] for r in selected['orders']}
        for k in ['order_items','payments','shipments','returns']:
            selected[k]=[r for r in tables[k] if r['order_id'] in order_ids]
        ships={r['shipment_id'] for r in selected['shipments']}; returns={r['return_id'] for r in selected['returns']}; pays={r['payment_id'] for r in selected['payments']}
        selected['shipment_items']=[r for r in tables['shipment_items'] if r['shipment_id'] in ships]
        selected['return_items']=[r for r in tables['return_items'] if r['return_id'] in returns]
        selected['refunds']=[r for r in tables['refunds'] if r['payment_id'] in pays]
        labels=[]; lineage=[]; summary=Counter()
        def label(table,record,col,observed,clean,action,issue,profile,reason,**extra):
            unseen=profile=='D' and issue in ['header_alias','phone_format','money_format','datetime_format','null_token']
            item={'case_id':f'{split}-{len(labels)+1:06d}','input_file':f'{table}.csv','record_number':record,'column':col,'observed_value':observed,'clean_value':clean,'expected_action':action,'issue_type':issue,'profile':profile,'pattern_group':'unseen' if unseen else 'seen','reason':reason,**extra}
            if action in ['normalize','keep','map_column']: item['expected_value']=clean
            labels.append(item);summary[action]+=1
        for table,rows in selected.items():
            fields=schema['tables'][table]['fields']; names=[f['name'] for f in fields]; pk=names[0]
            # Reorder columns in evaluation, preserving stable CSV record positions in private labels.
            column_order=list(names)
            if split=='evaluation': rng.shuffle(column_order)
            aliases={n: (KOREAN_HEADERS[n] if split=='development' else n.replace('_',' ').title()) for n in names}
            for n in names:
                label(table,0,aliases[n],aliases[n],n,'map_column','header_alias','D' if split=='evaluation' else 'A','원본 컬럼 의미를 표준 스키마에 일대일 연결',canonical_column=n)
            entries=[{'row':dict(r),'source':r,'duplicate':False} for r in rows]
            # Duplicate scenarios in each split; both rows require review, no automatic deletion.
            duplicate_sources=set()
            if table in ['customers','orders','order_items'] and rows:
                for index in [0,1]:
                    source_row=rows[index]; duplicate_sources.add(source_row[pk]); duplicate=dict(source_row)
                    if index==1:
                        change={'customers':'customer_name','orders':'delivery_memo','order_items':'quantity'}[table]
                        duplicate[change]='다른 담당자 입력' if change!='quantity' else str(int(duplicate[change])+1)
                    entries.append({'row':duplicate,'source':source_row,'duplicate':True})
            rng.shuffle(entries)
            matrix=[[aliases[n] for n in column_order]]
            used_review=set(); normal_controls=Counter()
            for index,entry in enumerate(entries,1):
                original=entry['source']; row=entry['row']; source_pk=original[pk]
                profile=('ABCD' if split=='evaluation' else 'ABC')[(index-1)%(4 if split=='evaluation' else 3)]
                lineage.append({'input_file':f'{table}.csv','record_number':index,'clean_pk':source_pk,'duplicate_copy':entry['duplicate'],'profile':profile})
                if source_pk in duplicate_sources:
                    label(table,index,'*',{aliases[n]:row[n] for n in column_order},original,'review','duplicate_pk',profile,'동일 PK의 원본/추가 행 모두 보류. 어느 행이 정본인지 입력만으로 결정하지 않음',canonical_column='*')
                else:
                    # Review examples take priority over formatting and occupy distinct cells.
                    for field in fields:
                        n=field['name']; value=row[n]; issue=None; dirty=None; reason=''
                        if n=='phone' and value and 'truncated_phone' not in used_review:
                            issue,dirty,reason='truncated_phone',value[:8],'번호 일부 손실. 원번호를 추측하지 않음'
                        elif n=='phone' and 'ambiguous_missing' not in used_review:
                            issue,dirty,reason='ambiguous_missing','0','0은 미입력·무효·오입력 중 의미가 불명확'
                        elif n=='customer_name' and 'required_missing' not in used_review:
                            issue,dirty,reason='required_missing','','필수 고객명 결측. 다른 값으로 추측 보충하지 않음'
                        elif table=='order_items' and n=='sku_id' and 'unknown_fk' not in used_review:
                            issue,dirty,reason='unknown_fk','SKU999999','대상 SKU가 기준표에 없음. 유사 ID로 임의 연결 금지'
                        elif table=='order_items' and n=='quantity' and 'zero_quantity' not in used_review:
                            issue,dirty,reason='zero_quantity','0','양수 수량 규칙 위반. 금액만 보고 수량 복원 금지'
                        elif table=='orders' and n=='order_total' and 'total_conflict' not in used_review:
                            issue,dirty,reason='total_conflict',str(int(value)+700),'합계 관계 모순. 주문/상세/배송비 중 어느 쪽이 오입력인지 확인 필요'
                        elif table=='orders' and n=='ordered_at' and 'ambiguous_date' not in used_review:
                            issue,dirty,reason='ambiguous_date','07/08/26 09:30','월/일 순서와 시간대가 불명확'
                        if issue:
                            used_review.add(issue);row[n]=dirty
                            label(table,index,aliases[n],dirty,value,'review',issue,profile,reason,canonical_column=n)
                            continue
                        # Explicit normal controls, protected from mutation.
                        control=None
                        if n=='discount_amount' and value=='0': control='valid_zero_discount'
                        if table=='categories' and n=='parent_category_id' and value=='': control='valid_root_null'
                        if table=='shipments' and n=='tracking_number' and original['shipment_status']=='preparing': control='valid_pending_tracking_null'
                        if table=='order_items' and n=='quantity' and int(value)>=50: control='valid_large_quantity'
                        if table=='customers' and n=='phone' and value=='': control='valid_optional_null'
                        if table=='order_items' and n=='unit_price':
                            sku=next(r for r in tables['product_variants'] if r['sku_id']==original['sku_id'])
                            if value!=sku['current_price']:control='valid_historical_price'
                        if control and normal_controls[control]<3:
                            normal_controls[control]+=1
                            label(table,index,aliases[n],value,value,'keep',control,profile,'업무 규칙상 정상. 오류 확정·삭제·임의 대체 금지',canonical_column=n)
                            continue
                        if rng.random()>=0.24:continue
                        dirty=value;issue=None;reason=''
                        if n in ['phone','recipient_phone'] and value:
                            digits=value.replace('-','')
                            dirty={'A':digits,'B':value.replace('-',' '),'C':'('+digits[:3]+') '+digits[3:7]+'-'+digits[7:],'D':'+82 '+digits[1:3]+' '+digits[3:7]+' '+digits[7:]}[profile]
                            issue,reason='phone_format','명시된 국가코드·구분자 정리, 숫자값 보존'
                        elif value=='' and field['nullable'] and n in ['phone','email','address_line2','delivery_memo']:
                            dirty={'A':'NULL','B':'없음','C':'N/A','D':' NULL '}[profile]
                            issue,reason='null_token','허용 컬럼의 승인된 결측 표현을 null로 통일'
                        elif field['type']=='integer' and n in ['current_price','order_total','unit_price','line_total','amount']:
                            dirty={'A':f'{int(value):,}','B':value+'원','C':f'{int(value):,} 원','D':'₩ '+f'{int(value):,}'}[profile]
                            issue,reason='money_format','KRW 단위·구분자만 제거, 원래 숫자값 보존'
                        elif field['type']=='datetime' and value:
                            dirty=value.replace('T',' ')
                            if profile in ['B','C']:dirty=dirty[:10].replace('-','/')+dirty[10:]
                            if profile=='D':dirty=dirty[:10].replace('-','.')+dirty[10:]
                            issue,reason='datetime_format','연-월-일 순서 및 +09:00 명시. 구분자만 ISO로 통일'
                        elif field.get('enum') and value:
                            dirty=value.upper();issue,reason='enum_case','명시된 범주와 대소문자만 다름'
                        elif n in ['customer_name','product_name','brand_name'] and value:
                            dirty=' '+value+' ';issue,reason='outer_whitespace','이름/브랜드 바깥 공백만 제거'
                        if issue and dirty!=value:
                            row[n]=dirty
                            label(table,index,aliases[n],dirty,value,'normalize',issue,profile,reason,canonical_column=n)
                matrix.append([row[n] for n in column_order])
            bundles.append({'split':split,'table':table,'matrix':matrix,'canonical_columns':column_order})
        truthdir=output/'evaluation_private'/split;truthdir.mkdir(parents=True,exist_ok=True)
        for name,data in [('cases',labels),('row-lineage',lineage)]:
            (truthdir/f'{name}.jsonl').write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in data))
        save(truthdir/'baseline.json',selected)
        private_manifest['splits'][split]={'customers':len(ids),'orders':len(selected['orders']),'baseline_rows':{k:len(v) for k,v in selected.items()},'cases':len(labels),'actions':dict(summary),'issue_types':dict(Counter(x['issue_type'] for x in labels)),'profiles':dict(Counter(x['profile'] for x in lineage))}
    save(output/'evaluation_private/manifest.json',private_manifest)
    save(output/'matrices.json',bundles)
    print(json.dumps(private_manifest['splits'],ensure_ascii=False,indent=2))

if __name__=='__main__':build(sys.argv[1],sys.argv[2])
