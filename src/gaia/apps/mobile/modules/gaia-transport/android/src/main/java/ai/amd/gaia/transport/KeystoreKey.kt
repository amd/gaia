// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

package ai.amd.gaia.transport

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

/**
 * The vault's AES-256-GCM key, made in the Android Keystore and never exportable. No user
 * authentication, like the Keychain item's AfterFirstUnlockThisDeviceOnly: the vault's files sit in
 * credential-encrypted storage, readable after the first unlock.
 */
internal object KeystoreKey {
  private const val PROVIDER = "AndroidKeyStore"
  private const val ALIAS = "ai.amd.gaia.paired-pc"

  @Synchronized
  fun get(): SecretKey {
    val store = KeyStore.getInstance(PROVIDER)
    store.load(null)
    (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
    val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, PROVIDER)
    generator.init(
      KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
        .setKeySize(256)
        .setRandomizedEncryptionRequired(true)
        .build(),
    )
    return generator.generateKey()
  }
}
