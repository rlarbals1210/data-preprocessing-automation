"""Read exported CSVs independently. Exit nonzero on schema/business violations."""
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def validate(root):
    root = Path(root)
    schema = json.loads((root / 'schema.json').read_text())
    manifest = json.loads((root / 'data/manifest.json').read_text())
    errors = []
    checks = 0

    def check(ok, rule, detail=''):
        nonlocal checks
        checks += 1
        if not ok:
            errors.append({'rule': rule, 'detail': detail})

    tables, maps = {}, {}
    snapshot = datetime.fromisoformat(manifest['snapshot_at'])
    for table, definition in schema['tables'].items():
        file = root / 'data/clean' / (table + '.csv')
        with file.open(newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            check(reader.fieldnames == [x['name'] for x in definition['fields']], 'headers', table)
            rows = list(reader)
        tables[table] = rows
        pk = definition['fields'][0]['name']
        check(len({r[pk] for r in rows}) == len(rows), 'unique_pk', table)
        maps[table] = {r[pk]: r for r in rows}
        check(len(rows) == manifest['files'][table]['rows'], 'manifest_count', table)
        check(hashlib.sha256(file.read_bytes()).hexdigest() == manifest['files'][table]['sha256'], 'manifest_hash', table)
        for r in rows:
            for field in definition['fields']:
                name = field['name']; value = r[name]; where = f'{table}:{r[pk]}:{name}'
                check(value != '' or field['nullable'], 'required', where)
                if not value:
                    continue
                if field['type'] == 'integer':
                    check(bool(re.fullmatch(r'\d+', value)), 'nonnegative_integer', where)
                    if name in {'quantity', 'weight_g'}:
                        check(int(value) > 0, 'positive', where)
                if field['type'] == 'datetime':
                    try:
                        t = datetime.fromisoformat(value)
                        check(value.endswith('+09:00') and t <= snapshot, 'snapshot_time', where)
                    except ValueError:
                        check(False, 'datetime', where)
                if 'enum' in field:
                    check(value in field['enum'], 'enum', where)
                if name in {'phone', 'recipient_phone'}:
                    check(bool(re.fullmatch(r'010-\d{4}-\d{4}', value)), 'phone_format', where)
                if name == 'postal_code':
                    check(bool(re.fullmatch(r'\d{5}', value)), 'postal_format', where)
                if name == 'email':
                    check(value.endswith('@example.com'), 'synthetic_email', where)
    for table, definition in schema['tables'].items():
        for r in tables[table]:
            for field in definition['fields']:
                if 'references' in field and r[field['name']]:
                    target = field['references']['table']
                    check(r[field['name']] in maps[target], 'foreign_key', f'{table}:{field["name"]}')
    # Avoid misleading successful business checks after structurally invalid input.
    structural = {'headers', 'unique_pk', 'required', 'nonnegative_integer', 'datetime', 'foreign_key'}
    if any(e['rule'] in structural for e in errors):
        return {'passed': False, 'checks': checks, 'errors': errors}

    def group(table, key):
        result = defaultdict(list)
        for r in tables[table]:
            result[r[key]].append(r)
        return result

    def num(r, k):
        return int(r[k])

    def before(a, b):
        return bool(a and b) and datetime.fromisoformat(a) <= datetime.fromisoformat(b)

    for table, rows in tables.items():
        for r in rows:
            start = r.get('created_at', r.get('joined_at'))
            if start and r.get('updated_at'):
                check(before(start, r['updated_at']), 'update_chronology', table)
    for cid, addresses in group('customer_addresses', 'customer_id').items():
        check(sum(r['is_default'] == 'true' for r in addresses) <= 1, 'one_default_address', cid)
    for r in tables['categories']:
        p = r['parent_category_id']
        if p:
            check(p != r['category_id'] and not maps['categories'][p]['parent_category_id'], 'category_two_levels', r['category_id'])
    variants = group('product_variants','product_id')
    for p in tables['products']:
        check(bool(variants[p['product_id']]), 'product_has_sku', p['product_id'])
    items = group('order_items', 'order_id')
    payments = group('payments', 'order_id')
    shipments = group('shipments', 'order_id')
    shipment_items = group('shipment_items', 'shipment_id')
    returns = group('return_items', 'return_id')
    refunds = group('refunds', 'payment_id')
    for r in tables['order_items']:
        check(num(r,'line_total') == num(r,'quantity') * num(r,'unit_price') - num(r,'discount_amount'), 'line_total', r['order_item_id'])
    for o in tables['orders']:
        oid=o['order_id']; successful=[p for p in payments[oid] if p['payment_status']=='succeeded']
        check(bool(items[oid]), 'order_has_items', oid)
        check(num(o,'order_total') == sum(num(x,'line_total') for x in items[oid])+num(o,'shipping_fee'), 'order_total', oid)
        check('2026-07-01' <= o['ordered_at'][:10] <= '2026-09-30', 'order_period', oid)
        check(before(maps['customers'][o['customer_id']]['joined_at'],o['ordered_at']), 'customer_before_order', oid)
        check(before(o['ordered_at'],o['updated_at']), 'order_updated', oid)
        check(len(successful)<=1, 'single_success_payment', oid)
        if o['order_status'] in {'paid','processing','completed'}:
            check(len(successful)==1, 'paid_order_has_payment',oid)
        if o['order_status']=='pending_payment':
            check(not successful and not shipments[oid], 'pending_order',oid)
        check(bool(o['cancelled_at']) == (o['order_status']=='cancelled'), 'cancel_timestamp',oid)
        if o['cancelled_at']:
            check(before(o['ordered_at'],o['cancelled_at']), 'cancel_after_order',oid)
            check(not shipments[oid], 'cancel_before_shipment',oid)
        if o['order_status']=='completed':
            check(bool(shipments[oid]) and all(s['shipment_status']=='delivered' for s in shipments[oid]), 'completed_delivered',oid)
    for p in tables['payments']:
        o=maps['orders'][p['order_id']]
        check(before(o['ordered_at'],p['requested_at']), 'payment_request',p['payment_id'])
        check(bool(p['paid_at']) == (p['payment_status']=='succeeded'), 'payment_timestamp',p['payment_id'])
        check(bool(p['failure_code']) == (p['payment_status']=='failed'), 'payment_failure_code',p['payment_id'])
        if p['paid_at']:
            check(before(p['requested_at'],p['paid_at']) and num(p,'amount')==num(o,'order_total'), 'paid_amount_time',p['payment_id'])
    allocated=Counter()
    for s in tables['shipments']:
        oid=s['order_id']; o=maps['orders'][oid]; ss=s['shipment_status']
        check(bool(shipment_items[s['shipment_id']]), 'shipment_has_items',s['shipment_id'])
        check(before(o['ordered_at'],s['created_at']), 'shipment_created',s['shipment_id'])
        if ss in {'shipped','delivered'}:
            check(bool(s['tracking_number'] and s['carrier_code']) and before(s['created_at'],s['shipped_at']), 'shipped_fields',s['shipment_id'])
            ps=[p for p in payments[oid] if p['payment_status']=='succeeded']
            check(len(ps)==1 and before(ps[0]['paid_at'],s['shipped_at']), 'paid_before_shipping',s['shipment_id'])
        else:
            check(not s['shipped_at'] and not s['delivered_at'], 'unshipped_dates',s['shipment_id'])
        check(bool(s['delivered_at'])==(ss=='delivered'), 'delivery_timestamp',s['shipment_id'])
        if ss=='delivered':
            check(before(s['shipped_at'],s['delivered_at']), 'delivery_time',s['shipment_id'])
        for si in shipment_items[s['shipment_id']]:
            item=maps['order_items'][si['order_item_id']]
            check(item['order_id']==oid,'shipment_same_order',si['shipment_item_id'])
            if ss!='cancelled': allocated[item['order_item_id']]+=num(si,'quantity')
    for item in tables['order_items']:
        count=allocated[item['order_item_id']]
        check(count<=num(item,'quantity'), 'shipment_quantity',item['order_item_id'])
        if maps['orders'][item['order_id']]['order_status']=='completed':
            check(count==num(item,'quantity'), 'complete_quantity',item['order_item_id'])
    returned=Counter(); approved_amount={}
    for r in tables['returns']:
        rid=r['return_id']; rs=r['return_status']; amount=0
        check(bool(returns[rid]), 'return_has_items',rid)
        check(bool(r['received_at'])==(rs in {'received','closed'}), 'return_received_at',rid)
        check(bool(r['closed_at'])==(rs in {'closed','rejected','cancelled'}), 'return_closed_at',rid)
        if r['received_at']: check(before(r['requested_at'],r['received_at']), 'return_receipt_time',rid)
        if r['closed_at']: check(before(r['received_at'] or r['requested_at'],r['closed_at']), 'return_close_time',rid)
        for ri in returns[rid]:
            si=maps['shipment_items'][ri['shipment_item_id']];s=maps['shipments'][si['shipment_id']];item=maps['order_items'][si['order_item_id']]
            check(s['order_id']==r['order_id'] and s['shipment_status']=='delivered','return_same_order_delivered',ri['return_item_id'])
            check(before(s['delivered_at'],r['requested_at']),'return_after_delivery',rid)
            if rs not in {'rejected','cancelled'}: returned[si['shipment_item_id']]+=num(ri,'quantity')
            amount+=num(item,'line_total')*num(ri,'quantity')//num(item,'quantity')
        approved_amount[rid]=amount
    for siid,q in returned.items(): check(q<=num(maps['shipment_items'][siid],'quantity'),'return_quantity',siid)
    successful_return_refunds=Counter()
    for f in tables['refunds']:
        p=maps['payments'][f['payment_id']];o=maps['orders'][p['order_id']]
        check(p['payment_status']=='succeeded' and before(p['paid_at'],f['requested_at']),'refund_payment',f['refund_id'])
        check(bool(f['refunded_at'])==(f['refund_status']=='succeeded'),'refund_timestamp',f['refund_id'])
        check(bool(f['failure_code'])==(f['refund_status']=='failed'),'refund_failure_code',f['refund_id'])
        if f['refunded_at']: check(before(f['requested_at'],f['refunded_at']),'refund_time',f['refund_id'])
        if f['refund_type']=='return':
            r=maps['returns'].get(f['return_id'])
            check(bool(r) and r['order_id']==p['order_id'],'refund_same_order',f['refund_id'])
            if r:
                check(r['return_status'] in {'received','closed'} and before(r['received_at'],f['requested_at']),'refund_after_receipt',f['refund_id'])
                check(num(f,'amount')==approved_amount[r['return_id']],'refund_return_amount',f['refund_id'])
                if f['refund_status']=='succeeded':successful_return_refunds[r['return_id']]+=num(f,'amount')
        else:
            check(not f['return_id'] and o['order_status']=='cancelled' and before(o['cancelled_at'],f['requested_at']) and num(f,'amount')==num(o,'order_total'),'refund_cancellation',f['refund_id'])
    for pid,rows in refunds.items():check(sum(num(x,'amount') for x in rows if x['refund_status']=='succeeded')<=num(maps['payments'][pid],'amount'),'refund_cap',pid)
    for r in tables['returns']:
        if r['return_status']=='closed':check(successful_return_refunds[r['return_id']]==approved_amount[r['return_id']],'closed_return_refunded',r['return_id'])
    return {'passed':not errors,'checks':checks,'errors':errors,'row_counts':{t:len(rows) for t,rows in tables.items()},'order_statuses':dict(Counter(r['order_status'] for r in tables['orders'])),'normal_examples':{'zero_discount_rows':sum(r['discount_amount']=='0' for r in tables['order_items']),'missing_customer_phones':sum(not r['phone'] for r in tables['customers']),'large_quantity_rows':sum(num(r,'quantity')>=50 for r in tables['order_items']),'historical_price_differences':sum(num(r,'unit_price')!=num(maps['product_variants'][r['sku_id']],'current_price') for r in tables['order_items'])}}


if __name__=='__main__':
    root=Path(sys.argv[1] if len(sys.argv)>1 else Path(__file__).resolve().parents[1])
    report=validate(root)
    (root/'reports/validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({**report,'error_count':len(report['errors']),'errors':report['errors'][:10]},ensure_ascii=False,indent=2))
    sys.exit(0 if report['passed'] else 1)
