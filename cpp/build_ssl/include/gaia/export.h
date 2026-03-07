
#ifndef GAIA_API_H
#define GAIA_API_H

#ifdef GAIA_CORE_STATIC_DEFINE
#  define GAIA_API
#  define GAIA_CORE_NO_EXPORT
#else
#  ifndef GAIA_API
#    ifdef gaia_core_EXPORTS
        /* We are building this library */
#      define GAIA_API 
#    else
        /* We are using this library */
#      define GAIA_API 
#    endif
#  endif

#  ifndef GAIA_CORE_NO_EXPORT
#    define GAIA_CORE_NO_EXPORT 
#  endif
#endif

#ifndef GAIA_CORE_DEPRECATED
#  define GAIA_CORE_DEPRECATED __declspec(deprecated)
#endif

#ifndef GAIA_CORE_DEPRECATED_EXPORT
#  define GAIA_CORE_DEPRECATED_EXPORT GAIA_API GAIA_CORE_DEPRECATED
#endif

#ifndef GAIA_CORE_DEPRECATED_NO_EXPORT
#  define GAIA_CORE_DEPRECATED_NO_EXPORT GAIA_CORE_NO_EXPORT GAIA_CORE_DEPRECATED
#endif

/* NOLINTNEXTLINE(readability-avoid-unconditional-preprocessor-if) */
#if 0 /* DEFINE_NO_DEPRECATED */
#  ifndef GAIA_CORE_NO_DEPRECATED
#    define GAIA_CORE_NO_DEPRECATED
#  endif
#endif

#endif /* GAIA_API_H */
