import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'

// 前端 lint 的第一版配置。
//
// 背景：`package.json` 里一直有 `"lint": "eslint ."`，但 devDependencies 里没有 eslint，
// CI（ci.yml）也只能打印 "skipping lint" —— 一个悬空脚本，给人「我们 lint 过了」的错觉。
// 这一版先把这个错觉消掉：只启用 typescript-eslint 的 recommended，且不把任何规则升级为
// 阻断项之外的额外负担；真正想收紧时再往 rules 里加，不要一上来就开满。
//
// react-hooks 是后补的：没有它，条件调用 hooks、依赖数组漏项都只能靠人眼。
// 补之前实测存量是 0 error，所以加进来不会一上来就红一片。
export default tseslint.config(
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      'run-compiled-tests.mjs',
      'vite.config.ts',
    ],
  },
  ...tseslint.configs.recommended.map((config) => ({
    ...config,
    files: ['src/**/*.{ts,tsx}'],
  })),
  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      // 只启用传统两条。插件 v6 的 `configs.recommended` 还带上了
      // react-hooks/refs、set-state-in-effect、purity 这一套 React Compiler 规则，
      // 存量代码有 46 处会红（"ref 镜像最新值"这类写法本来是有意的），
      // 不适合一次性套上来。真要收紧时单独评估。
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      // any 在这份代码里用得不少，一次性清完不现实，先不拦。
      '@typescript-eslint/no-explicit-any': 'off',
      // TS 编译器本身会报错，eslint 再报一遍是噪音。
      'no-undef': 'off',
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
)
