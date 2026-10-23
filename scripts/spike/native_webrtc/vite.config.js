import { defineConfig } from 'vite';
import { viteExternalsPlugin } from 'vite-plugin-externals';

// Matches the official sample's external GFN dependency. This direct-only
// probe does not load the GDN/GFN cloud script.
export default defineConfig({
  plugins: [viteExternalsPlugin({ GFN: 'GFN' })],
});
