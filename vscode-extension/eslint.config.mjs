import tseslint from 'typescript-eslint';

export default tseslint.config(
	{
		ignores: ['dist/**', 'out/**'],
	},
	...tseslint.configs.recommendedTypeChecked,
	{
		files: ['src/**/*.ts', 'src/**/*.tsx'],
		languageOptions: {
			parserOptions: {
				projectService: true,
				tsconfigRootDir: import.meta.dirname,
			},
		},
		rules: {
			curly: 'error',
			eqeqeq: ['error', 'always'],
			'no-throw-literal': 'error',
			'@typescript-eslint/consistent-type-imports': 'error',
			'@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
		},
	},
	{
		// `describe` and `it` from node:test return promises that the runner
		// owns; awaiting them at the top level of a suite is not a thing.
		files: ['src/test/**/*.ts'],
		rules: {
			'@typescript-eslint/no-floating-promises': 'off',
		},
	},
);
