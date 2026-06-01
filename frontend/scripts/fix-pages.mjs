import fs from 'fs';
import path from 'path';

const pagesDir = path.resolve('src/pages');

for (const file of fs.readdirSync(pagesDir).filter((f) => f.endsWith('.jsx'))) {
  const p = path.join(pagesDir, file);
  let content = fs.readFileSync(p, 'utf8');

  content = content.replace(/<StatCard`n/g, '<StatCard\n');

  if (!content.includes('<StatCard')) continue;

  content = content.replace(/^import StatCard from '\.\.\/components\/ui\/StatCard';\n/gm, '');

  const importLine = "import StatCard from '../components/ui/StatCard';\n";
  const anchor = content.match(/^import useLivePoll[^\n]+\n/m)
    || content.match(/^import PageHeader[^\n]+\n/m)
    || content.match(/^import React[^\n]+\n/m);

  if (anchor) {
    content = content.replace(anchor[0], anchor[0] + importLine);
  } else {
    content = importLine + content;
  }

  fs.writeFileSync(p, content);
  console.log('import added:', file);
}
