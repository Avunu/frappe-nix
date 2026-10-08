// The desk bundle's source: Vite names its output spa.bundle.[hash].js. A
// Vite source is never named *.bundle.* under public/, or frappe's esbuild
// would compile it a second time (docs/app-standards/assets.md).
import "./spa.css";

export const greet = (name: string): string => `Hello, ${name}`;
