-keepattributes *Annotation*
-dontwarn org.conscrypt.**

# The optional licensed adapter is loaded by name so the public build can omit it.
-keep class ai.clinicianassist.personalstate.SamsungHealthDataSdkAdapter {
    *;
}
