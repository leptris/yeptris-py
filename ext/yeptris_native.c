/* yeptris_native.c — fused RFC 8259 -> PyObject (TODO.restructure/41).
 *
 * The port of the Ruby native materializer (yeptris-ruby's
 * json_ruby.c): a hand-driven descent over the exported scan kernels
 * (scan/json.h), NOT the visit vtable — the perf ledger measured the
 * vtable 2.2x slower in Ruby; the kernel descent is the proven
 * shape. One 8-byte-prefix key cache (item 38) holds INTERNED keys:
 * Python caches str hashes on the object and dict probes compare
 * repeated keys — identical objects make dict probes pointer-equal.
 *
 * Python 3.9+ ABI. GIL held throughout (refcounted object building).
 * No GC games: refcounting replaces Ruby's page-growth mechanism. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "scan/json.h"
#include "parse/scalars.h"

#define YEP_JP_MAX 1000
#define YEP_KC 1024

/* Cache slots carry the fill-time key BYTES inline: the probe is
 * memcmp against the slot copy — no CPython unicode introspection
 * (PyUnicode_AsUTF8AndSize is 3.10+ stable-ABI; the 3.9 floor rules
 * it out), no allocation on either path. Keys longer than the copy
 * are not cache-eligible. */
#define YEP_KEY_MAX 48

typedef struct {
    uint64_t h;
    uint32_t len;
    char cp[YEP_KEY_MAX];
    PyObject* key;
} kcent;

typedef struct {
    const char* p;
    size_t len;
    size_t i;
    char* scratch;
    size_t scratch_cap;
    int depth;
    int err;    /* 0 ok, -1 memory (Python error set), -2 syntax */
    size_t errpos;
    kcent kc[YEP_KC];
} jp;

static PyObject* JSONDecodeError;
static int yep_cache_mode = 0; /* 0 on, 1 off (YEPTRIS_NATIVE_CACHE) */

static void jp_ws(jp* j) {
    while (j->i < j->len) {
        unsigned char c = (unsigned char)j->p[j->i];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') j->i++;
        else break;
    }
}

static uint64_t jp_hash(const char* sp, size_t sl) {
    /* 8-byte-prefix mix (the leptris nametab trick, item 38): one
     * safe load of min(8, len) + multiply. */
    uint64_t k = 0;
    uint64_t take = (uint64_t)sl < 8 ? (uint64_t)sl : 8;
    memcpy(&k, sp, (size_t)take);
    k ^= (uint64_t)sl * 0x9E3779B97F4A7C15ull;
    k *= 0xC2B2AE3D27D4EB4Full;
    k ^= k >> 29;
    return k;
}

static PyObject* jp_cached(jp* j, const char* sp, size_t sl) {
    uint64_t h = jp_hash(sp, sl);
    kcent* e = &j->kc[(uint32_t)(h & (YEP_KC - 1))];
    if (e->key != NULL && e->h == h && e->len == (uint32_t)sl &&
        memcmp(e->cp, sp, sl) == 0) {
        Py_INCREF(e->key);
        return e->key;
    }
    PyObject* s = PyUnicode_DecodeUTF8(sp, (Py_ssize_t)sl, NULL);
    if (s == NULL) {
        PyErr_Clear();
        j->err = -2;
        j->errpos = j->i;
        return NULL;
    }
    /* NO interning: the cache itself returns the identical object for
     * repeated keys, so dict probes already take the pointer-equality
     * fast path — registering in the interned dict is pure overhead
     * on unique-key documents (measured 2x on a flat str:str corpus) */
    Py_XDECREF(e->key);
    e->h = h;
    e->len = (uint32_t)sl;
    memcpy(e->cp, sp, sl);
    e->key = s; /* the table owns this reference */
    Py_INCREF(s);
    return s;
}

static PyObject* jp_str(jp* j, int as_key) {
    size_t start = j->i, close = 0;
    int has_esc = 0;
    if (!yep_json_string(j->p, j->len, &j->i, &close, &has_esc)) {
        j->err = -2;
        j->errpos = start;
        return NULL;
    }
    const char* sp;
    size_t sl;
    if (has_esc) {
        uint32_t span = (uint32_t)(close - start - 1);
        if ((size_t)span + 1 > j->scratch_cap) {
            size_t cap = j->scratch_cap ? j->scratch_cap : 64;
            while (cap < (size_t)span + 1) cap *= 2;
            char* ns = realloc(j->scratch, cap);
            if (ns == NULL) { j->err = -1; return NULL; }
            j->scratch = ns;
            j->scratch_cap = cap;
        }
        sl = yep_finish_double_into(j->p, (uint32_t)(start + 1), (uint32_t)close, j->scratch, span);
        sp = j->scratch;
    } else {
        sp = j->p + start + 1;
        sl = close - start - 1;
    }
    if (yep_cache_mode == 0 && sl <= YEP_KEY_MAX && (as_key || sl <= 24)) {
        return jp_cached(j, sp, sl);
    }
    PyObject* s = PyUnicode_DecodeUTF8(sp, (Py_ssize_t)sl, NULL);
    if (s == NULL) {
        PyErr_Clear();
        j->err = -2;
        j->errpos = start;
        return NULL;
    }
    return s;
}

static PyObject* jp_num(jp* j) {
    size_t start = j->i;
    int shape = 0;
    int64_t iv = 0;
    double dv = 0.0;
    /* the fused kernel: ONE grammar walk, values out */
    if (!yep_json_number_scan(j->p, j->len, &j->i, &shape, &iv, &dv)) {
        j->err = -2;
        j->errpos = start;
        return NULL;
    }
    if (shape == 0) return PyLong_FromLongLong(iv);
    if (shape == 1) return PyFloat_FromDouble(dv);
    /* integer text beyond int64: exact int from the validated span
     * (json.loads' behavior); absurd lengths degrade to double. */
    size_t n = j->i - start;
    if (n < 512) {
        char buf[512];
        memcpy(buf, j->p + start, n);
        buf[n] = '\0';
        return PyLong_FromString(buf, NULL, 10);
    }
    return PyFloat_FromDouble(dv);
}

static PyObject* jp_value(jp* j);

static PyObject* jp_object(jp* j) {
    j->i++;
    j->depth++;
    PyObject* d = PyDict_New();
    if (d == NULL) { j->err = -1; j->depth--; return NULL; }
    jp_ws(j);
    if (j->i < j->len && j->p[j->i] == '}') { j->i++; j->depth--; return d; }
    for (;;) {
        jp_ws(j);
        if (j->i >= j->len || j->p[j->i] != '"') { j->err = -2; j->errpos = j->i; goto fail; }
        PyObject* key = jp_str(j, 1);
        if (key == NULL) goto fail;
        jp_ws(j);
        if (j->i >= j->len || j->p[j->i] != ':') {
            Py_DECREF(key);
            j->err = -2;
            j->errpos = j->i;
            goto fail;
        }
        j->i++;
        PyObject* val = jp_value(j);
        if (val == NULL) { Py_DECREF(key); goto fail; }
        int rc = PyDict_SetItem(d, key, val);
        Py_DECREF(key);
        Py_DECREF(val);
        if (rc < 0) { j->err = -1; goto fail; }
        jp_ws(j);
        if (j->i >= j->len) { j->err = -2; j->errpos = j->i; goto fail; }
        if (j->p[j->i] == ',') { j->i++; continue; }
        if (j->p[j->i] == '}') { j->i++; break; }
        j->err = -2;
        j->errpos = j->i;
        goto fail;
    }
    j->depth--;
    return d;
fail:
    Py_DECREF(d);
    j->depth--;
    return NULL;
}

static PyObject* jp_array(jp* j) {
    j->i++;
    j->depth++;
    PyObject* a = PyList_New(0);
    if (a == NULL) { j->err = -1; j->depth--; return NULL; }
    jp_ws(j);
    if (j->i < j->len && j->p[j->i] == ']') { j->i++; j->depth--; return a; }
    for (;;) {
        PyObject* v = jp_value(j);
        if (v == NULL) goto fail;
        int rc = PyList_Append(a, v);
        Py_DECREF(v);
        if (rc < 0) { j->err = -1; goto fail; }
        jp_ws(j);
        if (j->i >= j->len) { j->err = -2; j->errpos = j->i; goto fail; }
        if (j->p[j->i] == ',') { j->i++; continue; }
        if (j->p[j->i] == ']') { j->i++; break; }
        j->err = -2;
        j->errpos = j->i;
        goto fail;
    }
    j->depth--;
    return a;
fail:
    Py_DECREF(a);
    j->depth--;
    return NULL;
}

static PyObject* jp_value(jp* j) {
    if (j->depth >= YEP_JP_MAX) { j->err = -2; j->errpos = j->i; return NULL; }
    jp_ws(j);
    if (j->i >= j->len) { j->err = -2; j->errpos = j->i; return NULL; }
    char c = j->p[j->i];
    if (c == '{') return jp_object(j);
    if (c == '[') return jp_array(j);
    if (c == '"') return jp_str(j, 0);
    if (c == 't' || c == 'f' || c == 'n') {
        const char* word = c == 't' ? "true" : (c == 'f' ? "false" : "null");
        if (!yep_json_literal(j->p, j->len, &j->i, word)) {
            j->err = -2;
            j->errpos = j->i;
            return NULL;
        }
        if (c == 't') Py_RETURN_TRUE;
        if (c == 'f') Py_RETURN_FALSE;
        Py_RETURN_NONE;
    }
    if (c == 'N' || c == 'I') {
        /* json.loads' non-standard constants (accepted by default):
         * NaN, Infinity — RFC 8259 rejects them, parity keeps them */
        const char* word = c == 'N' ? "NaN" : "Infinity";
        if (!yep_json_literal(j->p, j->len, &j->i, word)) {
            j->err = -2;
            j->errpos = j->i;
            return NULL;
        }
        return PyFloat_FromDouble(c == 'N' ? NAN : INFINITY);
    }
    if (c == '-' && j->i + 1 < j->len && j->p[j->i + 1] == 'I') {
        if (!yep_json_literal(j->p, j->len, &j->i, "-Infinity")) {
            j->err = -2;
            j->errpos = j->i;
            return NULL;
        }
        return PyFloat_FromDouble(-INFINITY);
    }
    if (c == '-' || (c >= '0' && c <= '9')) return jp_num(j);
    j->err = -2;
    j->errpos = j->i;
    return NULL;
}

static void jp_release(jp* j) {
    for (int k = 0; k < YEP_KC; k++) Py_XDECREF(j->kc[k].key);
    free(j->scratch);
}

static PyObject* loads_error(jp* j, const char* data, Py_ssize_t dlen) {
    if (j->err == -1 && PyErr_Occurred()) return NULL; /* memory: keep it */
    PyErr_Clear();
    PyObject* doc = PyUnicode_DecodeUTF8(data, dlen, "replace");
    if (doc == NULL) return NULL;
    PyObject* exc = PyObject_CallFunction(JSONDecodeError, "sOn", "invalid JSON",
                                          doc, (Py_ssize_t)j->errpos);
    Py_DECREF(doc);
    return exc; /* NULL propagates the call failure */
}

static PyObject* py_loads(PyObject* self, PyObject* args) {
    (void)self;
    PyObject* obj;
    if (!PyArg_ParseTuple(args, "O", &obj)) return NULL;
    const char* data;
    Py_ssize_t dlen;
    PyObject* keepalive = NULL;
    if (PyUnicode_Check(obj)) {
        /* limited-API safe: PyUnicode_AsUTF8AndSize is 3.10+ stable */
        keepalive = PyUnicode_AsUTF8String(obj);
        if (keepalive == NULL) return NULL;
        data = PyBytes_AsString(keepalive);
        dlen = PyBytes_Size(keepalive);
    } else if (PyBytes_Check(obj)) {
        data = PyBytes_AsString(obj); /* limited-API safe (abi3 wheels) */
        dlen = PyBytes_Size(obj);
        if (data == NULL) return NULL;
    } else {
        PyErr_SetString(PyExc_TypeError, "loads() expects str or bytes");
        return NULL;
    }
    jp j;
    memset(&j, 0, sizeof(j));
    j.p = data;
    j.len = (size_t)dlen;
    j.i = 0;
    PyObject* v = jp_value(&j);
    Py_XDECREF(keepalive); /* the parse never retains the input buffer */
    if (v != NULL) {
        jp_ws(&j);
        if (j.i != (size_t)dlen) {
            j.err = -2;
            j.errpos = j.i;
            Py_DECREF(v);
            v = NULL;
        }
    }
    if (v == NULL) {
        PyObject* exc = loads_error(&j, data, dlen);
        jp_release(&j);
        if (exc == NULL) return NULL;
        PyErr_SetObject(JSONDecodeError, exc);
        Py_DECREF(exc);
        return NULL;
    }
    jp_release(&j);
    return v;
}

static PyObject* py_cache_mode(PyObject* self, PyObject* args) {
    (void)self;
    (void)args;
    return PyLong_FromLong(yep_cache_mode == 0 ? 1 : 0);
}

static PyMethodDef methods[] = {
    {"loads", py_loads, METH_VARARGS, "Parse strict JSON text into Python objects."},
    {"cache_mode", py_cache_mode, METH_NOARGS, "1 when the token cache is on."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "yeptris._native",
    "Fused RFC 8259 scanner/materializer over libyeptris kernels.",
    -1,
    methods,
    NULL, NULL, NULL, NULL,
};

PyMODINIT_FUNC PyInit__native(void) {
    const char* cm = getenv("YEPTRIS_NATIVE_CACHE");
    if (cm != NULL && strcmp(cm, "off") == 0) yep_cache_mode = 1;
    PyObject* m = PyModule_Create(&moduledef);
    if (m == NULL) return NULL;
    PyObject* jd = PyImport_ImportModule("json.decoder");
    if (jd == NULL) { Py_DECREF(m); return NULL; }
    JSONDecodeError = PyObject_GetAttrString(jd, "JSONDecodeError");
    Py_DECREF(jd);
    if (JSONDecodeError == NULL) { Py_DECREF(m); return NULL; }
    return m;
}
