// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Pool {
    function x() external pure {
        revert("not allowed here");
    }
    function y() external pure {
        revert("bad");
    }
}
