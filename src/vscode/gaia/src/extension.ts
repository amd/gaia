/*
Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
*/

import * as vscode from "vscode";
import { GaiaChatModelProvider } from "./provider";

export function activate(context: vscode.ExtensionContext) {
	const ext = vscode.extensions.getExtension("amd.gaia-vscode");
	const extVersion = ext?.packageJSON?.version ?? "unknown";
	const vscodeVersion = vscode.version;
	// Keep UA minimal: only extension version and VS Code version
	const ua = `gaia-vscode/${extVersion} VSCode/${vscodeVersion}`;

	const provider = new GaiaChatModelProvider(context.secrets, ua);
	// Register the GAIA provider under the vendor id used in package.json
	vscode.lm.registerLanguageModelChatProvider("gaia", provider);

	// Management command to configure server settings
	context.subscriptions.push(
		vscode.commands.registerCommand("gaia.manage", async () => {
			const existing = await context.secrets.get("gaia.serverUrl");
			const defaultUrl = "http://localhost:8080";
			const serverUrl = await vscode.window.showInputBox({
				title: "GAIA Server URL",
				prompt: existing ? "Update your GAIA server URL" : "Enter your GAIA server URL",
				ignoreFocusOut: true,
				value: existing ?? defaultUrl,
			});
			if (serverUrl === undefined) {
				return; // user canceled
			}
			if (!serverUrl.trim()) {
				await context.secrets.delete("gaia.serverUrl");
				vscode.window.showInformationMessage("GAIA server URL reset to default.");
				return;
			}
			await context.secrets.store("gaia.serverUrl", serverUrl.trim());
			vscode.window.showInformationMessage("GAIA server URL saved.");
		})
	);
}

export function deactivate() {}
