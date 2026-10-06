import { compileFromFile } from 'json-schema-to-typescript';
import { writeFile } from 'node:fs/promises';
const source = await compileFromFile('src/contracts.schema.json', { bannerComment: '/* Generated from PreAct Core. Run npm run contracts; do not edit manually. */' });
await writeFile('src/contracts.ts', source);
