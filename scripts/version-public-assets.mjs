import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';

const root = new URL('../web/public/', import.meta.url);
for (const name of await readdir(root)) {
    if (!name.endsWith('.html')) continue;
    const file = new URL(name, root);
    const original = await readFile(file, 'utf8');
    let versioned = original;
    for (const match of original.matchAll(/(?:src|href)="(\/static\/[^"?]+\.(?:js|css))(?:\?v=[^"&]+)?"/g)) {
        const asset = await readFile(new URL(match[1].slice(1), root));
        const version = createHash('sha256').update(asset).digest('hex').slice(0, 12);
        const updated = match[0].replace(/=".*"$/, `="${match[1]}?v=${version}"`);
        versioned = versioned.replaceAll(match[0], updated);
    }
    if (process.argv.includes('--check')) {
        if (versioned !== original) throw new Error(`${name}: run npm run assets:sync`);
    } else if (versioned !== original) {
        await writeFile(file, versioned);
    }
}
