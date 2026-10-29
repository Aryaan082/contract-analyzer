// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ID1 {
    address public ID2;
    address public ID3;
    uint256 public ID4;

    constructor(address ID5, address ID6) {
        ID2 = ID5;
        ID3 = ID6;
        ID4 = 250;
    }

    modifier ID7() {
        require(msg.sender == ID2, "not admin");
        _;
    }

    function ID8(uint256 ID9) external ID7 {
        require(ID9 <= 1000, "too high");
        ID4 = ID9;
    }

    function ID10(address ID11) external {
        ID3 = ID11;
    }

    function ID12(uint256 ID13) external view returns (uint256 ID14, uint256 ID15) {
        ID14 = ID13 * ID4 / 10_000;
        ID15 = ID13 - ID14;
    }
}
