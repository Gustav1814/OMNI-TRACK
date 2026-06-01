import fs from 'fs';
import path from 'path';

const dir = path.resolve('src/pages');

for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.jsx'))) {
  const p = path.join(dir, f);
  let c = fs.readFileSync(p, 'utf8');
  c = c.replace(/<StatCard`n/g, '<StatCard\n');
  if (c.includes('<StatCard') && !c.includes("import StatCard")) {
    c = c.replace(/^(import .+\n)/m, "$1import StatCard from '../components/ui/StatCard';\n");
  }
  fs.writeFileSync(p, c);
  if (c.includes('StatCard')) console.log('ok', f);
}
