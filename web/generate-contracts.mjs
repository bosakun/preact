import { compileFromFile } from 'json-schema-to-typescript';
import { writeFile } from 'node:fs/promises';
const source = await compileFromFile('src/contracts.schema.json', { bannerComment: '/* Generated from PreAct Core. Run npm run contracts; do not edit manually. */' });
await writeFile('src/contracts.ts', source);
const playground = await compileFromFile('src/playground.schema.json', { bannerComment: '/* Generated from PreAct playground contracts. Run npm run contracts; do not edit manually. */' });
await writeFile('src/playground-contracts.ts', playground);
