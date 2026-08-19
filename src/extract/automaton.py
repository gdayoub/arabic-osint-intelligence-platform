"""aho corasick string matching.

the problem. i have a gazetteer with a few thousand names and a document
with a few thousand characters. the obvious way is loop over the names and
call text.find on each one. that is O(names times text) and it gets slower
every time i add a name to the list.

aho corasick finds all of them in one pass. cost is O(text + total pattern
length + number of matches) and adding more names does not make scanning
any slower. that property is the whole reason i did this instead of a loop.

how it works. three pieces.

1. a trie of every pattern. walking a character moves me down one edge.

2. failure links. say patterns are [حسن، سنة] and i am at حس and the next
   character is ن so i reach حسن. i just consumed سن which is the start of
   سنة. a plain trie would throw that away and restart from the root and
   miss it. the failure link on حسن points at س ن inside the other branch
   so i keep going without ever backing up in the text.

   the failure link of a node always points at the longest proper suffix of
   what i have matched so far that is also a prefix of some pattern.

3. output links. a node reports its own pattern plus everything its failure
   chain reports. that is how i get overlapping matches like حسن and سن at
   the same position without a second scan.

i build the failure links breadth first because a node's link only depends
on nodes closer to the root so by the time i reach a node its parent is
already done.

i wrote this instead of installing pyahocorasick because the brief says no
dependency without justification and this is about eighty lines. the real
package is a C extension and would be faster. if scanning ever shows up in
a benchmark that is the swap.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Match:
    """one hit. start and end index into whatever string i scanned."""

    start: int
    end: int
    pattern: str
    payload: object


class _Node:
    # slots because there is one of these per character across every pattern
    # and the dict per node would dominate memory otherwise
    __slots__ = ("children", "fail", "outputs")

    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.fail: _Node | None = None
        # patterns that end here. a list because several patterns can share
        # an end node once failure links get merged in
        self.outputs: list[tuple[str, object]] = []


class Automaton:
    """build once then scan many documents against it."""

    def __init__(self) -> None:
        self._root = _Node()
        self._built = False
        self._pattern_count = 0

    def add(self, pattern: str, payload: object = None) -> None:
        """put a pattern in the trie. has to happen before build."""
        if self._built:
            raise RuntimeError("cannot add patterns after build")
        if not pattern:
            return

        node = self._root
        for char in pattern:
            node = node.children.setdefault(char, _Node())
        node.outputs.append((pattern, payload))
        self._pattern_count += 1

    def build(self) -> None:
        """wire up the failure links. breadth first from the root."""
        queue: deque[_Node] = deque()

        # depth one fails to the root. there is no shorter suffix than empty
        for child in self._root.children.values():
            child.fail = self._root
            queue.append(child)

        while queue:
            node = queue.popleft()
            for char, child in node.children.items():
                # walk up my own failure chain looking for someone who can
                # take this character. the root always can because falling
                # off it just means starting over
                candidate = node.fail
                while candidate is not None and char not in candidate.children:
                    candidate = candidate.fail
                child.fail = candidate.children[char] if candidate else self._root
                if child.fail is child:
                    child.fail = self._root

                # inherit whatever my failure target reports. doing it here
                # at build time means scanning never has to walk the chain
                child.outputs.extend(child.fail.outputs)
                queue.append(child)

        self._built = True

    def scan(self, text: str) -> list[Match]:
        """one pass. returns every match including overlapping ones."""
        if not self._built:
            raise RuntimeError("call build() before scan()")

        matches: list[Match] = []
        node = self._root

        for index, char in enumerate(text):
            # if this character does not fit follow failure links until it
            # does or until i fall back to the root. this loop is why i never
            # have to move backwards through the text
            while node is not None and char not in node.children:
                node = node.fail
            node = self._root if node is None else node.children[char]

            for pattern, payload in node.outputs:
                start = index - len(pattern) + 1
                matches.append(Match(start, index + 1, pattern, payload))

        return matches

    def __len__(self) -> int:
        return self._pattern_count
