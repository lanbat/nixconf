package com.lanbat.presence;

/**
 * Decodes an Apple iBeacon from the manufacturer-specific payload Android hands
 * back for company id 0x004C.
 *
 * <p>An iBeacon payload is {@code 02 15 <16-byte UUID> <major:2> <minor:2>
 * <txpower:1>}: the first byte is the AD type (0x02, "service data") and the
 * second is the length (0x15 = 21). Only the UUID identifies the phone here.
 */
public final class BeaconParser {
    private BeaconParser() {
    }

    /** An iBeacon UUID as it is stored in the config: lower case, no dashes.
     *  Dashes and case in the deployment's {@code bleBeacon} do not matter. */
    public static String normalize(String uuid) {
        if (uuid == null) {
            return null;
        }
        return uuid.replace("-", "").trim().toLowerCase(java.util.Locale.ROOT);
    }

    /** The iBeacon's 16-byte UUID as {@code xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx},
     *  or null when {@code manufacturerData} is not an iBeacon. */
    public static String uuid(byte[] manufacturerData) {
        if (manufacturerData == null || manufacturerData.length < 18) {
            return null;
        }
        if ((manufacturerData[0] & 0xFF) != 0x02 || (manufacturerData[1] & 0xFF) != 0x15) {
            return null;
        }
        StringBuilder sb = new StringBuilder(36);
        for (int i = 2; i < 18; i++) {
            if (i == 6 || i == 8 || i == 10 || i == 12) {
                sb.append('-');
            }
            sb.append(Character.forDigit((manufacturerData[i] >> 4) & 0xF, 16));
            sb.append(Character.forDigit(manufacturerData[i] & 0xF, 16));
        }
        return sb.toString();
    }
}
