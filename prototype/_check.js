// 临时校验：抽出原型里的内联 JS 做语法检查，并核对元素 id 引用
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');

const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.log('FAIL: 没有 script 块'); process.exit(1); }

try {
  new Function(m[1]);
  console.log('JS 语法 OK (' + m[1].length + ' 字符)');
} catch (e) {
  console.log('语法错误: ' + e.message);
  process.exit(1);
}

const ids = [...html.matchAll(/id="([\w-]+)"/g)].map(x => x[1]);
const used = [...m[1].matchAll(/getElementById\(['"]([\w-]+)['"]\)/g)].map(x => x[1]);
const missing = used.filter(u => !ids.includes(u));
console.log(missing.length ? '缺失 id: ' + missing.join(', ') : 'DOM id 引用 OK (' + used.join(', ') + ')');

for (const tag of ['div', 'nav', 'section', 'article', 'table']) {
  const open = (html.match(new RegExp('<' + tag + '[\\s>]', 'g')) || []).length;
  const close = (html.match(new RegExp('</' + tag + '>', 'g')) || []).length;
  if (open !== close) console.log('WARN: <' + tag + '> 开 ' + open + ' 闭 ' + close);
}
console.log('结构检查完成');
