import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { Workbook } from '@oai/artifact-tool';

const out = path.resolve(process.argv[2] || './project');
await fs.mkdir(path.join(out,'data/clean'), {recursive:true});
await fs.mkdir(path.join(out,'docs'), {recursive:true});
await fs.mkdir(path.join(out,'reports'), {recursive:true});
const definitions = {
 customers:'customer_id customer_name email? phone? customer_status marketing_consent joined_at updated_at',
 customer_addresses:'address_id customer_id address_label recipient_name recipient_phone postal_code address_line1 address_line2? is_default created_at updated_at',
 categories:'category_id parent_category_id? category_name is_active',
 products:'product_id category_id product_name brand_name description? product_status created_at updated_at',
 product_variants:'sku_id product_id option_name barcode? current_price weight_g? variant_status created_at updated_at',
 orders:'order_id customer_id ordered_at order_channel order_status recipient_name recipient_phone postal_code address_line1 address_line2? delivery_memo? shipping_fee order_total cancelled_at? updated_at',
 order_items:'order_item_id order_id sku_id product_name_snapshot option_name_snapshot quantity unit_price discount_amount line_total',
 payments:'payment_id order_id payment_method payment_status amount requested_at paid_at? failure_code?',
 shipments:'shipment_id order_id carrier_code? tracking_number? shipment_status created_at shipped_at? delivered_at?',
 shipment_items:'shipment_item_id shipment_id order_item_id quantity',
 returns:'return_id order_id return_status requested_at received_at? closed_at?',
 return_items:'return_item_id return_id shipment_item_id quantity reason_code reason_detail? item_condition',
 refunds:'refund_id payment_id return_id? refund_type refund_status amount requested_at refunded_at? failure_code?',
};
const enums = {
 customer_status:['active','inactive','withdrawn'], marketing_consent:['true','false','unknown'],
 product_status:['draft','active','discontinued'], variant_status:['active','out_of_stock','discontinued'],
 order_channel:['web','mobile_app','manual'], order_status:['pending_payment','paid','processing','completed','cancelled'],
 payment_method:['card','bank_transfer','easy_pay'], payment_status:['pending','succeeded','failed','cancelled'],
 shipment_status:['preparing','shipped','delivered','cancelled'],
 return_status:['requested','approved','rejected','received','closed','cancelled'],
 reason_code:['damaged','wrong_item','changed_mind','other'],item_condition:['unopened','opened','damaged','unknown'],
 refund_type:['order_cancellation','return'],refund_status:['pending','succeeded','failed'],
 is_default:['true','false'],is_active:['true','false'],
};
const integer = new Set('current_price weight_g shipping_fee order_total quantity unit_price discount_amount line_total amount'.split(' '));
const targets={customer_id:'customers',address_id:'customer_addresses',category_id:'categories',parent_category_id:'categories',product_id:'products',sku_id:'product_variants',order_id:'orders',order_item_id:'order_items',payment_id:'payments',shipment_id:'shipments',shipment_item_id:'shipment_items',return_id:'returns',return_item_id:'return_items',refund_id:'refunds'};
const labels={customers:'회원 1명',customer_addresses:'고객 저장 배송지 1개',categories:'카테고리 1개',products:'상품 1개',product_variants:'판매 옵션 SKU 1개',orders:'주문 1건',order_items:'주문 상품 항목 1개',payments:'결제 시도 1회',shipments:'출고 묶음 1개',shipment_items:'출고 배정 항목 1개',returns:'반품 접수 1건',return_items:'반품 대상 출고 항목 1개',refunds:'환불 시도 1회'};
const descriptions={customer_id:'고객 식별자',customer_name:'가상 고객명',email:'이메일',phone:'고객 연락처',customer_status:'고객 상태',marketing_consent:'마케팅 동의·거부·미확인',joined_at:'가입 시각',updated_at:'마지막 수정 시각',address_id:'저장 배송지 식별자',address_label:'배송지 별칭',recipient_name:'수령인',recipient_phone:'수령인 연락처',postal_code:'우편번호',address_line1:'기본 주소',address_line2:'상세 주소',is_default:'기본 배송지 여부',created_at:'생성 시각',category_id:'카테고리 식별자',parent_category_id:'상위 카테고리',category_name:'카테고리명',is_active:'활성 여부',product_id:'상품 식별자',product_name:'상품명',brand_name:'브랜드명',description:'상품 설명',product_status:'상품 상태',sku_id:'판매 옵션 식별자',option_name:'옵션 이름',barcode:'가상 바코드 식별 문자열; GS1 체크섬 검증 대상 아님',current_price:'스냅샷 시점 판매가격 KRW',weight_g:'그램 단위 무게',variant_status:'옵션 상태',order_id:'주문 식별자',ordered_at:'주문 시각',order_channel:'주문 경로',order_status:'주문 처리 상태; 반품 상태와 별개',delivery_memo:'배송 요청사항',shipping_fee:'최초 주문 배송비 KRW',order_total:'최초 주문금액 KRW',cancelled_at:'전체 취소 시각',order_item_id:'주문 항목 식별자',product_name_snapshot:'주문 당시 상품명',option_name_snapshot:'주문 당시 옵션명',quantity:'해당 테이블의 주문·출고·반품 수량',unit_price:'주문 당시 개당 가격 KRW',discount_amount:'주문 항목 전체 할인액 KRW',line_total:'할인 후 항목 금액 KRW',payment_id:'결제 시도 식별자',payment_method:'결제 수단',payment_status:'결제 시도 상태',amount:'해당 결제·환불 시도 금액 KRW',requested_at:'요청 시각',paid_at:'결제 성공 시각',failure_code:'실패 코드',shipment_id:'출고 묶음 식별자',carrier_code:'가상 택배사 코드',tracking_number:'가상 운송장',shipment_status:'출고·배송 상태',shipped_at:'출고 시각',delivered_at:'배송 완료 시각',shipment_item_id:'출고 항목 식별자',return_id:'반품 접수 식별자',return_status:'반품 처리 상태',received_at:'반품 입고 시각',closed_at:'반품 종료 시각; rejected/cancelled 포함',return_item_id:'반품 항목 식별자',reason_code:'반품 사유 분류',reason_detail:'자유 서술 반품 사유',item_condition:'반품 물품 상태',refund_id:'환불 시도 식별자',refund_type:'환불 원인',refund_status:'환불 처리 상태',refunded_at:'환불 성공 시각'};
const schema={version:'1.0.0',timezone:'Asia/Seoul',timestamp_format:'YYYY-MM-DDTHH:mm:ss+09:00',currency:'KRW',null_encoding:'empty field',tables:{}};
for(const [table,definition] of Object.entries(definitions)) {
 const fields=definition.split(' ').map((token,i)=>{const name=token.replace('?','');return {name,type:integer.has(name)?'integer':name.endsWith('_at')?'datetime':'string',nullable:token.endsWith('?'),primary_key:i===0,...(i>0&&targets[name]?{references:{table:targets[name],column:definitions[targets[name]].split(' ')[0]}}:{}),...(enums[name]?{enum:enums[name]}:{}),description:descriptions[name]};});
 schema.tables[table]={grain:labels[table],fields};
}
const d=Object.fromEntries(Object.keys(definitions).map(k=>[k,[]]));
let state=20261015;
const random=()=>{state=(Math.imul(state,1664525)+1013904223)>>>0;return state/4294967296;};
const pick=a=>a[Math.floor(random()*a.length)];
const id=(prefix,n)=>prefix+String(n).padStart(6,'0');
const date=(day,h=10,m=0)=>new Date(Date.UTC(2026,6,1+day,h-9,m)).toISOString().replace('Z','');
const stamp=(day,h=10,m=0)=>{const t=new Date(Date.UTC(2026,6,1+day,h,m));return t.toISOString().slice(0,19)+'+09:00';};
const add=(t,days=0,minutes=0)=>{const dt=new Date(Date.parse(t)+days*86400000+minutes*60000+9*3600000);return dt.toISOString().slice(0,19)+'+09:00';};
const pre='2026-06-01T09:00:00+09:00', updated='2026-10-01T09:00:00+09:00';
for(let i=1;i<=300;i++){
 const c={customer_id:id('CUS',i),customer_name:`가상고객${String(i).padStart(3,'0')}`,email:i%17?`customer${i}@example.com`:null,phone:i%19?`010-${String(1000+i).padStart(4,'0')}-${String(i*17).padStart(4,'0')}`:null,customer_status:i%11?'active':'inactive',marketing_consent:pick(enums.marketing_consent),joined_at:pre,updated_at:updated};d.customers.push(c);
 for(let j=0;j<(i<=150?2:1);j++)d.customer_addresses.push({address_id:id('ADR',d.customer_addresses.length+1),customer_id:c.customer_id,address_label:j?'회사':'집',recipient_name:c.customer_name,recipient_phone:c.phone||`010-9000-${String(i).padStart(4,'0')}`,postal_code:String(1000+i).padStart(5,'0'),address_line1:`가상시 테스트구 예제길 ${i}`,address_line2:i%4?`${j+1}층 ${i}호`:null,is_default:j?'false':'true',created_at:pre,updated_at:pre});
}
const roots=['뷰티','생활용품','주방용품'];
for(let i=0;i<3;i++)d.categories.push({category_id:id('CAT',i+1),parent_category_id:null,category_name:roots[i],is_active:'true'});
const names=['크림','토너','클렌저','선크림','세제','타월','수납함','방향제','머그','접시','도마','주방세제'];
names.forEach((n,i)=>d.categories.push({category_id:id('CAT',i+4),parent_category_id:id('CAT',Math.floor(i/4)+1),category_name:n,is_active:'true'}));
for(let i=1;i<=100;i++){
 const kind=(i-1)%12, p={product_id:id('PRD',i),category_id:id('CAT',kind+4),product_name:`${names[kind]} ${String(i).padStart(3,'0')}`,brand_name:['모아뷰티','모아리빙','모아키친'][Math.floor(kind/4)],description:i%10?`가상 쇼핑몰의 ${names[kind]} 예제 상품`:null,product_status:i%23?'active':'discontinued',created_at:pre,updated_at:updated};d.products.push(p);
 for(let j=0;j<2;j++)d.product_variants.push({sku_id:id('SKU',d.product_variants.length+1),product_id:p.product_id,option_name:j?'대용량':'기본',barcode:i%13?String(8800000000000+i*10+j):null,current_price:(15+(i%70)+j*10)*1000,weight_g:i%9?(100+i*5+j*50):null,variant_status:i%23?'active':'discontinued',created_at:pre,updated_at:updated});
}
const orderItems=new Map(), delivered=new Map(), successful=new Map();
for(let i=1;i<=1000;i++){
 const c=d.customers[(i*37)%300],a=d.customer_addresses.find(x=>x.customer_id===c.customer_id&&x.is_default==='true');
 const status=i<=900?(i%20===0?'cancelled':'completed'):i<=930?'processing':i<=960?'paid':i<=980?'pending_payment':'cancelled';
 const t=stamp(i<=900?(i*7)%86:91,8+i%10,i%60), orderId=id('ORD',i),items=[];
 for(let j=0;j<1+(i%4);j++){
  const sku=d.product_variants[(i*13+j*17)%200],p=d.products.find(x=>x.product_id===sku.product_id);
  const q=i%111===0&&j===0?60:1+Math.floor(random()*3),price=sku.current_price+(i%7===0?-1000:0),discount=(i%5===0?500:0)*q;
  const row={order_item_id:id('ITM',d.order_items.length+1),order_id:orderId,sku_id:sku.sku_id,product_name_snapshot:p.product_name,option_name_snapshot:sku.option_name,quantity:q,unit_price:price,discount_amount:discount,line_total:q*price-discount};d.order_items.push(row);items.push(row);
 }
 orderItems.set(orderId,items);const subtotal=items.reduce((s,x)=>s+x.line_total,0),shipping=subtotal>=50000?0:3000;
 const o={order_id:orderId,customer_id:c.customer_id,ordered_at:t,order_channel:pick(enums.order_channel),order_status:status,recipient_name:a.recipient_name,recipient_phone:a.recipient_phone,postal_code:a.postal_code,address_line1:i%31? a.address_line1:`가상시 이전구 옛길 ${i}`,address_line2:a.address_line2,delivery_memo:i%3?'문 앞에 놓아주세요':null,shipping_fee:shipping,order_total:subtotal+shipping,cancelled_at:status==='cancelled'?add(t,0,30):null,updated_at:add(t,status==='completed'?5:1)};d.orders.push(o);
 const method=pick(enums.payment_method);
 if(i%10===0)d.payments.push({payment_id:id('PAY',d.payments.length+1),order_id:orderId,payment_method:method,payment_status:'failed',amount:o.order_total,requested_at:add(t,0,1),paid_at:null,failure_code:'TEST_DECLINED'});
 const ps=status==='pending_payment'?'pending':i>980?'cancelled':'succeeded';
 const payment={payment_id:id('PAY',d.payments.length+1),order_id:orderId,payment_method:method,payment_status:ps,amount:o.order_total,requested_at:add(t,0,3),paid_at:ps==='succeeded'?add(t,0,4):null,failure_code:null};d.payments.push(payment);if(ps==='succeeded')successful.set(orderId,payment);
 if(status==='cancelled'&&ps==='succeeded'){
  d.refunds.push({refund_id:id('REF',d.refunds.length+1),payment_id:payment.payment_id,return_id:null,refund_type:'order_cancellation',refund_status:'succeeded',amount:o.order_total,requested_at:add(t,0,35),refunded_at:add(t,0,50),failure_code:null});
 }
 if(!['completed','processing'].includes(status))continue;
 const split=i%6===0&&items[0].quantity>1, shiprows=[];
 for(let part=0;part<(split?2:1);part++){
  const ss=status==='completed'?'delivered':(i%2?'shipped':'preparing');
  const sh={shipment_id:id('SHP',d.shipments.length+1),order_id:orderId,carrier_code:ss==='preparing'?null:'TEST_EXPRESS',tracking_number:ss==='preparing'?null:String(700000000000+d.shipments.length+1),shipment_status:ss,created_at:add(t,1),shipped_at:ss==='preparing'?null:add(t,2+part),delivered_at:ss==='delivered'?add(t,4+part):null};d.shipments.push(sh);
  for(let j=0;j<items.length;j++){
   const item=items[j],q=part===1?(j===0?item.quantity-1:0):split&&j===0?1:item.quantity;if(!q)continue;
   const si={shipment_item_id:id('SPI',d.shipment_items.length+1),shipment_id:sh.shipment_id,order_item_id:item.order_item_id,quantity:q};d.shipment_items.push(si);if(ss==='delivered')shiprows.push(si);
  }
 }
 if(status==='completed')delivered.set(orderId,shiprows);
}
const candidates=d.orders.filter(o=>o.order_status==='completed'&&orderItems.get(o.order_id).length>=2&&o.ordered_at<'2026-09-01').slice(0,60);
for(let k=0;k<candidates.length;k++){
 const o=candidates[k],sis=delivered.get(o.order_id),last=d.shipments.filter(s=>s.order_id===o.order_id).map(s=>s.delivered_at).sort().at(-1),status=['closed','received','approved','requested','rejected','cancelled'][k%6];
 const ret={return_id:id('RET',k+1),order_id:o.order_id,return_status:status,requested_at:add(last,2),received_at:['closed','received'].includes(status)?add(last,5):null,closed_at:['closed','rejected','cancelled'].includes(status)?add(last,7):null};d.returns.push(ret);let amount=0;
 for(let j=0;j<(k<20?2:1);j++){
  const si=sis[j],item=d.order_items.find(x=>x.order_item_id===si.order_item_id);d.return_items.push({return_item_id:id('RTI',d.return_items.length+1),return_id:ret.return_id,shipment_item_id:si.shipment_item_id,quantity:1,reason_code:pick(enums.reason_code),reason_detail:k%4?'테스트용 반품 요청':null,item_condition:['closed','received'].includes(status)?'opened':'unknown'});amount+=item.line_total/item.quantity;
 }
 if(['closed','received'].includes(status)){
  const pid=successful.get(o.order_id).payment_id;
  if(k%12===0)d.refunds.push({refund_id:id('REF',d.refunds.length+1),payment_id:pid,return_id:ret.return_id,refund_type:'return',refund_status:'failed',amount,requested_at:add(last,5,30),refunded_at:null,failure_code:'TEST_PROVIDER_TIMEOUT'});
  d.refunds.push({refund_id:id('REF',d.refunds.length+1),payment_id:pid,return_id:ret.return_id,refund_type:'return',refund_status:status==='closed'?'succeeded':'pending',amount,requested_at:add(last,6),refunded_at:status==='closed'?add(last,7):null,failure_code:null});
 }
}
await fs.writeFile(path.join(out,'schema.json'),JSON.stringify(schema,null,2)+'\n');
let dictionary='# 모아마켓 데이터 사전\n\n생성 데이터이며 실제 고객·상거래 기록이 아닙니다. 정의의 정본은 `schema.json`이며 이 문서는 생성기로 파생합니다.\n\n문자열 ID/전화/우편번호를 숫자로 읽지 마세요. CSV는 UTF-8(BOM 없음), 쉼표 구분, LF 줄바꿈, 빈 필드는 null입니다. 시각은 ISO 8601 +09:00, 금액은 원 단위 정수입니다.\n';
const manifest={schema_version:schema.version,seed:20261015,orders_from:'2026-07-01',orders_to:'2026-09-30',snapshot_at:'2026-10-15T00:00:00+09:00',synthetic:true,files:{}};
const wb=Workbook.create();
const csvCell=v=>{const s=v===null||v===undefined?'':String(v);return /[",\r\n]/.test(s)?'"'+s.replaceAll('"','""')+'"':s;};
for(const [name,rows] of Object.entries(d)){
 const fields=schema.tables[name].fields,headers=fields.map(f=>f.name),matrix=[headers,...rows.map(r=>headers.map(h=>r[h]??null))];
 const sh=wb.worksheets.add(name);
 fields.forEach((f,c)=>{if(f.type==='string'||f.type==='datetime')sh.getRangeByIndexes(1,c,rows.length,1).setNumberFormat('@');});
 sh.getRangeByIndexes(0,0,matrix.length,headers.length).values=matrix;
 sh.showGridLines=false;sh.freezePanes.freezeRows(1);sh.getRangeByIndexes(0,0,matrix.length,headers.length).format.columnWidth=22;sh.getRangeByIndexes(0,0,1,headers.length).format={fill:'#23364D',font:{bold:true,color:'#FFFFFF'}};
 fields.forEach((f,c)=>{if(f.type==='string'||f.type==='datetime')sh.getRangeByIndexes(1,c,rows.length,1).setNumberFormat('@');});
 // Artifact Tool owns the cell matrix; CSV serialization preserves raw stored values.
 const stored=sh.getRangeByIndexes(0,0,matrix.length,headers.length).values.map(row=>row.map(v=>v instanceof Date?new Date(v.getTime()+9*3600000).toISOString().slice(0,19)+'+09:00':v));
 if(JSON.stringify(stored)!==JSON.stringify(matrix))throw new Error(`Cell values changed on import: ${name}`);
 const csv=stored.map(row=>row.map(csvCell).join(',')).join('\n')+'\n';
 await fs.writeFile(path.join(out,'data/clean',name+'.csv'),csv);
 manifest.files[name]={rows:rows.length,columns:headers.length,sha256:crypto.createHash('sha256').update(csv).digest('hex')};
 dictionary+=`\n## ${name}\n\n${labels[name]}\n\n| 컬럼 | 타입 | null 허용 | 키·제약 | 의미 |\n|---|---|---|---|---|\n`;
 for(const f of fields)dictionary+=`| ${f.name} | ${f.type} | ${f.nullable?'예':'아니오'} | ${f.primary_key?'PK':f.references?'FK → '+f.references.table+'.'+f.references.column:f.enum?f.enum.join(' / '):''} | ${f.description} |\n`;
}
// Preview display formats do not alter the raw CSV serialization above.
for(const [name,definition] of Object.entries(schema.tables)){
 const sh=wb.worksheets.getItem(name);
 definition.fields.forEach((f,c)=>{
  const range=sh.getRangeByIndexes(1,c,d[name].length,1);
  if(f.type==='datetime')range.setNumberFormat('yyyy-mm-dd hh:mm');
  if(['barcode','tracking_number'].includes(f.name))range.setNumberFormat('0');
 });
}
wb.recalculate();
const previewDir=path.resolve(process.env.PREVIEW_DIR||'./previews');await fs.mkdir(previewDir,{recursive:true});
for(const name of (process.env.SKIP_PREVIEWS?[]:Object.keys(d))){
 const sh=wb.worksheets.getItem(name); const image=await wb.render({sheetName:name,range:'A1:D5',scale:1,format:'png'});await fs.writeFile(path.join(previewDir,name+'.png'),new Uint8Array(await image.arrayBuffer()));
}
await fs.writeFile(path.join(out,'docs/data-dictionary.md'),dictionary);
await fs.writeFile(path.join(out,'data/manifest.json'),JSON.stringify(manifest,null,2)+'\n');
console.log(JSON.stringify({output:out,files:manifest.files},null,2));
