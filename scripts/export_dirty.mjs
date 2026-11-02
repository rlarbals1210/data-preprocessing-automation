import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { Workbook } from '@oai/artifact-tool';

const root=path.resolve(process.argv[2]);
const bundles=JSON.parse(await fs.readFile(path.join(root,'matrices.json'),'utf8'));
const quote=v=>/[",\r\n]/.test(v)?'"'+v.replaceAll('"','""')+'"':v;
const csv=matrix=>matrix.map(row=>row.map(quote).join(',')).join('\n')+'\n';
const hashes={};
for(const b of bundles){
 const text=csv(b.matrix);
 const wb=await Workbook.fromCSV(text,{sheetName:b.table});
 const sh=wb.worksheets.getItem(b.table);
 wb.recalculate();
 const actual=sh.getRangeByIndexes(0,0,b.matrix.length,b.matrix[0].length).values.map(row=>row.map(v=>v===null?'':v instanceof Date?new Date(v.getTime()+9*3600000).toISOString().slice(0,19)+'+09:00':String(v)));
 if(JSON.stringify(actual)!==JSON.stringify(b.matrix))throw new Error(`Raw string roundtrip changed: ${b.split}/${b.table}`);
 const target=path.join(root,'data/dirty',b.split,b.table+'.csv');await fs.mkdir(path.dirname(target),{recursive:true});
 const content=csv(actual);await fs.writeFile(target,content);
 hashes[`${b.split}/${b.table}.csv`]={rows:actual.length-1,sha256:crypto.createHash('sha256').update(content).digest('hex')};
 if(!process.env.SKIP_PREVIEWS){
  sh.showGridLines=false;sh.freezePanes.freezeRows(1);
  sh.getRangeByIndexes(0,0,5,b.matrix[0].length).format.columnWidth=28;
  sh.getRangeByIndexes(0,0,1,b.matrix[0].length).format={fill:'#23364D',font:{bold:true,color:'#FFFFFF'}};
  b.matrix[0].forEach((h,c)=>{
   const canonical=b.canonical_columns[c];
   if(canonical.endsWith('_at'))sh.getRangeByIndexes(1,c,b.matrix.length-1,1).setNumberFormat('yyyy-mm-dd hh:mm');
   if(['barcode','tracking_number'].includes(canonical))sh.getRangeByIndexes(1,c,b.matrix.length-1,1).setNumberFormat('0');
  });
  wb.recalculate();
  const preview=await wb.render({sheetName:b.table,range:'A1:D5',scale:1,format:'png'});
  await fs.mkdir(path.join(root,'previews'),{recursive:true});
  await fs.writeFile(path.join(root,'previews',b.split+'-'+b.table+'.png'),new Uint8Array(await preview.arrayBuffer()));
 }
}
await fs.writeFile(path.join(root,'evaluation_private/input-manifest.json'),JSON.stringify(hashes,null,2)+'\n');
console.log(JSON.stringify({files:Object.keys(hashes).length,rows:Object.values(hashes).reduce((s,x)=>s+x.rows,0)}));
