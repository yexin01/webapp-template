import type { Linter } from "eslint";

export declare const bannedPackages: { group: string[]; message: string }[];
export declare const serverFiles: string[];
export declare function guardConfigs(root: string): Linter.Config[];
