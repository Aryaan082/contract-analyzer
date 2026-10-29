// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Traps {
    string constant A = "plain text one";
    string constant B = "plain text two";
    string constant C = 'plain text three';
    string constant D = "plain text four";
    string constant E = unicode"plain text five";
    uint256 public x = 1; /* note one */ // note two
    function f() external pure returns (uint256 r) {
        assembly {
            // a plain assembly comment
            let a := 1 /* note */
            r := add(a, 2) // a plain trailing note
        }
    }
    /* note 3 */ uint256 public y;
    // note four
    uint256 public z;
}
