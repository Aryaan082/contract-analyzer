// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Traps {
    string constant A = "// not a comment";
    string constant B = "/* nor this */";
    string constant C = 'it\'s "quoted" // still a string';
    string constant D = "a \" b /* c */ d";
    string constant E = unicode"café ❤ // x";
    uint256 public x = 1; /* trailing */ // another
    function f() external pure returns (uint256 r) {
        assembly {
            // a comment with a "quote
            let a := 1 /* } */
            r := add(a, 2) // trailing ' apostrophe
        }
    }
    /* a // b */ uint256 public y;
    // /* not a block start
    uint256 public z;
}
