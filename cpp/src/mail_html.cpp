// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Ported from html/parser.py (HTMLParser.goahead and the tag parsers it calls)
// and outgoing._TextExtractor. Feeding once and closing parses as one pass with
// end=True, so that is the only mode here.

#include "mail_html.h"

#include <cstring>
#include <set>

#include "mail_text.h"

namespace gaia::mail {

namespace {

constexpr size_t npos = std::string::npos;
constexpr const char* kSpace = "\t\n\r\f ";

bool in(char c, const char* set) { return c != '\0' && std::strchr(set, c) != nullptr; }

bool isAsciiAlpha(char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }

std::string lowerAscii(std::string s) {
    for (char& c : s) {
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    }
    return s;
}

// [\t\n\r\f /]* when `!keepFinalSlash`, else (?:[\t\n\r\f ]|/(?!>))*
size_t skipSpace(const std::string& s, size_t i, bool keepFinalSlash) {
    while (i < s.size()) {
        if (in(s[i], kSpace)) {
            ++i;
        } else if (s[i] == '/' && !(keepFinalSlash && i + 1 < s.size() && s[i + 1] == '>')) {
            ++i;
        } else {
            break;
        }
    }
    return i;
}

// One attribute of locatetagend / attrfind_tolerant at `i`: its end, or npos when none starts there.
size_t attribute(const std::string& s, size_t i, bool keepFinalSlash) {
    if (i >= s.size() || !in(s[i - 1], "'\"\t\n\r\f /") || in(s[i], "\t\n\r\f />")) return npos;
    for (++i; i < s.size() && !in(s[i], "\t\n\r\f /=>");) ++i;
    size_t j = i;
    while (j < s.size() && in(s[j], kSpace)) ++j;
    if (j < s.size() && s[j] == '=') {
        for (++j; j < s.size() && in(s[j], kSpace);) ++j;
        if (j < s.size() && (s[j] == '\'' || s[j] == '"')) {
            const size_t close = s.find(s[j], j + 1);
            if (close != npos) i = close + 1;
        } else {
            while (j < s.size() && !in(s[j], ">\t\n\r\f ")) ++j;
            i = j;
        }
    }
    return skipSpace(s, i, keepFinalSlash);
}

// The end of the tag name (tagfind_tolerant's group 1) starting at `i`.
size_t nameEnd(const std::string& s, size_t i) {
    for (++i; i < s.size() && !in(s[i], "\t\n\r\f />");) ++i;
    return i;
}

// locatetagend.match(s, i).end(), `i` at the name's first letter.
size_t locateTagEnd(const std::string& s, size_t i) {
    i = skipSpace(s, nameEnd(s, i), false);
    for (size_t next; (next = attribute(s, i, false)) != npos;) i = next;
    return i < s.size() && s[i] == '>' ? i + 1 : i;
}

// commentclose `--!?>` searched from `from`, else commentabruptclose `-?>` matched there.
size_t commentEnd(const std::string& s, size_t from) {
    for (size_t p = s.find("--", from); p != npos; p = s.find("--", p + 1)) {
        if (s.compare(p + 2, 1, ">") == 0) return p + 3;
        if (s.compare(p + 2, 2, "!>") == 0) return p + 4;
    }
    if (s.compare(from, 1, ">") == 0) return from + 1;
    if (s.compare(from, 2, "->") == 0) return from + 2;
    return npos;
}

// Just past the next '>' at or after `from`, else npos.
size_t pastGt(const std::string& s, size_t from) {
    const size_t gt = s.find('>', from);
    return gt == npos ? npos : gt + 1;
}

// parse_html_declaration for a `<!` that is not a comment.
size_t declarationEnd(const std::string& s, size_t i) {
    if (s.compare(i, 9, "<![CDATA[") == 0) {
        const size_t close = s.find("]]>", i + 9);
        return close == npos ? npos : close + 3;
    }
    if (lowerAscii(s.substr(i, 9)) == "<!doctype") return pastGt(s, i + 9);
    if (s.compare(i, 3, "<![") == 0) return pastGt(s, i + 3);
    return pastGt(s, i + 2);
}

class TextExtractor {
public:
    explicit TextExtractor(const std::string& html) : s_(html), lowered_(lowerAscii(html)) {}

    std::string run() {
        const size_t n = s_.size();
        size_t i = 0;
        while (i < n) {
            size_t j = n;
            if (cdata_.empty()) {
                j = s_.find('<', i);
                if (j == npos) j = n;
            } else if (cdata_ != "plaintext") {
                j = cdataClose(i);
                if (j == npos) break;
            }
            if (i < j) data(s_.substr(i, j - i), escapable_);
            i = j;
            if (i == n) break;
            size_t k;
            if (i + 1 < n && isAsciiAlpha(s_[i + 1])) {
                k = startTag(i);
            } else if (s_.compare(i, 2, "</") == 0) {
                k = endTag(i);
            } else if (s_.compare(i, 4, "<!--") == 0) {
                k = commentEnd(s_, i + 4);
            } else if (s_.compare(i, 2, "<?") == 0) {
                k = pastGt(s_, i + 2);
            } else if (s_.compare(i, 2, "<!") == 0) {
                k = declarationEnd(s_, i);
            } else {
                data("<", true);
                k = i + 1;
            }
            if (k == npos) {
                // Unterminated at the end: dropped, except a bare "</".
                if (i + 2 == n && s_.compare(i, 2, "</") == 0) data("</", true);
                k = n;
            }
            i = k;
        }
        if (i < n) data(s_.substr(i), escapable_);
        return out_;
    }

private:
    void data(const std::string& text, bool unescape) {
        if (!hidden_ && !text.empty()) out_ += unescape ? text::htmlUnescape(text) : text;
    }

    void opened(const std::string& tag) {
        static const std::set<std::string> kBreaks{"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"};
        if (kHidden.count(tag)) {
            ++hidden_;
        } else if (kBreaks.count(tag)) {
            out_ += "\n";
        }
    }

    void closed(const std::string& tag) {
        if (kHidden.count(tag) && hidden_) --hidden_;
    }

    // interesting.search for `</elem(?=[\t\n\r\f />])`, ASCII case folded.
    size_t cdataClose(size_t from) const {
        const std::string needle = "</" + cdata_;
        for (size_t p = lowered_.find(needle, from); p != npos; p = lowered_.find(needle, p + 1)) {
            const size_t after = p + needle.size();
            if (after < s_.size() && in(s_[after], "\t\n\r\f />")) return p;
        }
        return npos;
    }

    size_t startTag(size_t i) {
        const size_t endpos = locateTagEnd(s_, i + 1);
        if (s_[endpos - 1] != '>') return npos;
        const size_t name = nameEnd(s_, i + 1);
        const std::string tag = lowerAscii(s_.substr(i + 1, name - i - 1));
        size_t k = skipSpace(s_, name, true);
        for (size_t next; k < endpos && (next = attribute(s_, k, true)) != npos;) k = next;
        const std::string end = k < endpos ? text::strip(s_.substr(k, endpos - k)) : "";
        if (end != ">" && end != "/>") {
            data(s_.substr(i, endpos - i), false);
            return endpos;
        }
        opened(tag);
        if (end == "/>") {
            closed(tag);
            return endpos;
        }
        static const std::set<std::string> kRawText{"script", "style", "xmp", "iframe", "noembed", "noframes",
                                                     "plaintext"};
        if (kRawText.count(tag)) {
            cdata_ = tag;
            escapable_ = false;
        } else if (tag == "textarea" || tag == "title") {
            cdata_ = tag;
            escapable_ = true;
        }
        return endpos;
    }

    size_t endTag(size_t i) {
        if (s_.find('>', i + 2) == npos) return npos;
        if (!isAsciiAlpha(s_[i + 2])) return s_[i + 2] == '>' ? i + 3 : pastGt(s_, i + 2);
        const size_t j = locateTagEnd(s_, i + 2);
        if (s_[j - 1] != '>') return npos;
        closed(lowerAscii(s_.substr(i + 2, nameEnd(s_, i + 2) - i - 2)));
        cdata_.clear();
        escapable_ = true;
        return j;
    }

    inline static const std::set<std::string> kHidden{"script", "style", "head", "title"};

    const std::string& s_;
    const std::string lowered_;
    std::string out_;
    std::string cdata_;
    bool escapable_ = true;
    int hidden_ = 0;
};

}  // namespace

std::string htmlText(const std::string& html) {
    const std::string parts = TextExtractor(html).run();
    // re.sub(r"\n{3,}", "\n\n", ...)
    std::string squeezed;
    size_t run = 0;
    for (char c : parts) {
        run = c == '\n' ? run + 1 : 0;
        if (run <= 2) squeezed += c;
    }
    return text::strip(squeezed);
}

}  // namespace gaia::mail
